# -*- coding: utf-8 -*-

# Copyright (c) 2010-2011, GEM Foundation.
#
# OpenQuake is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# only, as published by the Free Software Foundation.
#
# OpenQuake is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License version 3 for more details
# (a copy is included in the LICENSE file that accompanied this code).
#
# You should have received a copy of the GNU Lesser General Public License
# version 3 along with OpenQuake.  If not, see
# <http://www.gnu.org/licenses/lgpl-3.0.txt> for a copy of the LGPLv3 License.

"""Logic tree parser, verifier and processor."""

import os
import sys
import re
import random
import itertools
from decimal import Decimal

from lxml import etree

from openquake.nrml import nrml_schema_file
from openquake.java import jvm


class LogicTreeError(Exception):
    def __init__(self, filename, basepath, msg):
        super(LogicTreeError, self).__init__(msg)
        self.filename = filename
        self.basepath = basepath

    def get_filepath(self):
        return os.path.join(self.basepath, self.filename)

    def __str__(self):
        return 'file %r: %s' % (self.get_filepath(), self.message)


class ParsingError(LogicTreeError):
    pass


class ValidationError(LogicTreeError):
    def __init__(self, node, *args, **kwargs):
        super(ValidationError, self).__init__(*args, **kwargs)
        self.lineno = node.sourceline

    def __str__(self):
        return 'file %r line %r: %s' % (self.get_filepath(), self.lineno,
                                        self.message)


class Branch(object):
    def __init__(self, branch_id, weight, value):
        self.branch_id = branch_id
        self.weight = weight
        self.value = value
        self.child_branchset = None


class BranchSet(object):
    def __init__(self, uncertainty_type, filters):
        self.branches = []
        self.uncertainty_type = uncertainty_type
        self.filters = filters

    def sample(self, rnd=random):
        diceroll = rnd.random()
        acc = 0
        for branch in self.branches:
            acc += branch.weight
            if acc >= diceroll:
                return branch
        raise AssertionError('do weights really sum up to 1.0?')

    def apply_uncertainty(self, value, source):
        PointSource = jvm().JClass('org.opensha.sha.earthquake.'\
                'rupForecastImpl.GEM1.SourceData.GEMPointSourceData')
        AreaSource = jvm().JClass('org.opensha.sha.earthquake.'\
                'rupForecastImpl.GEM1.SourceData.GEMAreaSourceData')
        # TODO: handle exceptions in java methods and rethrow LogicTreeError

        if not isinstance(source, (PointSource, AreaSource)):
            # simple fault or complex fault - only one mfd always
            mfdlist = [source.getMfd()]
        elif isinstance(source, PointSource):
            # point
            mfdlist = source.getHypoMagFreqDistAtLoc().getMagFreqDistList()
        else:
            # area
            mfdlist = source.getMagfreqDistFocMech().getMagFreqDistList()

        if self.uncertainty_type in ('abGRAbsolute', 'maxMagGRAbsolute'):
            valuelist = value
        else:
            valuelist = itertools.repeat(value)

        for mfd, mfd_value in itertools.izip(mfdlist, valuelist):
            self._apply_uncertainty_to_mfd(mfd, mfd_value)

    def _apply_uncertainty_to_mfd(self, mfd, value):
        if self.uncertainty_type == 'abGRAbsolute':
            a, b = value
            mfd.setAB(float(a), float(b))
        elif self.uncertainty_type == 'bGRRelative':
            mfd.incrementB(float(value))
        elif self.uncertainty_type == 'maxMagGRRelative':
            mfd.incrementMagUpper(float(value))
        elif self.uncertainty_type == 'maxMagGRAbsolute':
            mfd.setMagUpper(float(value))
        else:
            raise AssertionError('what is %s btw?' % self.uncertainty_type)


class BaseLogicTree(object):
    NRML = 'http://openquake.org/xmlns/nrml/0.2'
    FILTERS = ('applyToTectonicRegionType',
               'applyToSources',
               'applyToSourceType')

    _xmlschema = None

    @classmethod
    def get_xmlschema(cls):
        if not cls._xmlschema:
            cls._xmlschema = etree.XMLSchema(file=nrml_schema_file())
        return cls._xmlschema

    def __init__(self, basepath, filename):
        self.basepath = basepath
        self.filename = filename
        parser = etree.XMLParser(schema=self.get_xmlschema())
        self.branches = {}
        self.open_ends = set()
        filestream = self._open_file(self.filename)
        try:
            tree = etree.parse(filestream, parser=parser)
        except etree.XMLSyntaxError as exc:
            raise ParsingError(self.filename, self.basepath, str(exc))
        [tree] = tree.getroot()
        self.root_branchset = None
        self.parse_tree(tree)

    def parse_tree(self, tree_node):
        for depth, branchinglevel_node in enumerate(tree_node):
            self.parse_branchinglevel(branchinglevel_node, depth)
        self.root_branchset = self.validate_tree(tree_node,
                                                 self.root_branchset)

    def _open_file(self, filename):
        try:
            return open(os.path.join(self.basepath, filename))
        except IOError as exc:
            raise ParsingError(filename, self.basepath, str(exc))

    def parse_branchinglevel(self, branchinglevel_node, depth):
        new_open_ends = set()
        for number, branchset_node in enumerate(branchinglevel_node):
            branchset = self.parse_branchset(branchset_node)
            branchset = self.validate_branchset(branchset_node, depth, number,
                                                branchset)
            self.parse_branches(branchset_node, branchset)
            if depth == 0 and number == 0:
                self.root_branchset = branchset
            else:
                self.apply_branchset(branchset_node, branchset)
            for branch in branchset.branches:
                new_open_ends.add(branch)
        self.open_ends.clear()
        self.open_ends.update(new_open_ends)

    def parse_branchset(self, branchset_node):
        uncertainty_type = branchset_node.get('uncertaintyType')
        filters = dict((filtername, branchset_node.get(filtername))
                       for filtername in self.FILTERS
                       if filtername in branchset_node.attrib)
        filters = self.validate_filters(branchset_node, uncertainty_type,
                                        filters)
        branchset = BranchSet(uncertainty_type, filters)
        return branchset

    def parse_branches(self, branchset_node, branchset):
        weight_sum = 0
        for branchnode in branchset_node:
            weight = branchnode.find('{%s}uncertaintyWeight' % self.NRML).text
            weight = Decimal(weight.strip())
            weight_sum += weight
            value_node = branchnode.find('{%s}uncertaintyModel' % self.NRML)
            value = self.validate_uncertainty_value(
                value_node, branchset, value_node.text.strip()
            )
            branch_id = branchnode.get('branchID')
            branch = Branch(branch_id, weight, value)
            if branch_id in self.branches:
                raise ValidationError(
                    branchnode, self.filename, self.basepath,
                    "branchID %r is not unique" % branch_id
                )
            self.branches[branch_id] = branch
            branchset.branches.append(branch)
        if weight_sum != 1.0:
            raise ValidationError(
                branchset_node, self.filename, self.basepath,
                "branchset weights don't sum up to 1.0"
            )

    def validate_tree(self, tree_node, root_branchset):
        return root_branchset

    def validate_uncertainty_value(self, node, branchset, value):
        raise NotImplementedError()

    def validate_filters(self, node, uncertainty_type, filters):
        raise NotImplementedError()

    def validate_branchset(self, branchset_node, depth, number, branchset):
        raise NotImplementedError()

    def apply_branchset(self, branchset_node, branchset):
        for branch in self.open_ends:
            branch.child_branchset = branchset


class SourceModelLogicTree(BaseLogicTree):
    SOURCE_TYPES = ('point', 'area', 'complexFault', 'simpleFault')

    def __init__(self, *args, **kwargs):
        self.source_ids_to_num_mfds = {}
        self.tectonic_region_types = set()
        self.source_types = set()
        super(SourceModelLogicTree, self).__init__(*args, **kwargs)

    def validate_uncertainty_value(self, node, branchset, value):
        _float_re = re.compile(r'^(\+|\-)?(\d+|\d*\.\d+)$')
        if branchset.uncertainty_type == 'sourceModel':
            self.collect_source_model_data(value)
            return os.path.join(self.basepath, value)
        elif branchset.uncertainty_type == 'abGRAbsolute' \
                or branchset.uncertainty_type == 'maxMagGRAbsolute':
            [source_id] = branchset.filters['applyToSources']
            num_numbers_expected = self.source_ids_to_num_mfds[source_id]
            assert num_numbers_expected != 0
            if branchset.uncertainty_type == 'abGRAbsolute':
                num_numbers_expected *= 2
            values = []
            for single_number in value.split():
                if not _float_re.match(single_number):
                    break
                values.append(float(single_number))
                if len(values) > num_numbers_expected:
                    break
            else:
                if len(values) == num_numbers_expected:
                    if branchset.uncertainty_type == 'abGRAbsolute':
                        return zip(*((iter(values), ) * 2))
                    else:
                        return values
            raise ValidationError(
                node, self.filename, self.basepath,
                'expected list of %d float(s) separated by space, ' \
                'as source %r has %d MFD(s)' % (num_numbers_expected,
                source_id, self.source_ids_to_num_mfds[source_id])
            )
        else:
            if not _float_re.match(value):
                raise ValidationError(
                    node, self.filename, self.basepath,
                    'expected single float value'
                )
            return float(value)

    def validate_filters(self, branchset_node, uncertainty_type, filters):
        if uncertainty_type == 'sourceModel' and filters:
            raise ValidationError(
                branchset_node, self.filename, self.basepath,
                'filters are not allowed on source model uncertainty'
            )
        if len(filters) > 1:
            raise ValidationError(
                branchset_node, self.filename, self.basepath,
                "only one filter is allowed per branchset"
            )
        if 'applyToSources' in filters:
            source_ids = filters['applyToSources'].split()
            nonexistent_source_ids = set(source_ids)
            nonexistent_source_ids.difference_update(
                self.source_ids_to_num_mfds
            )
            if nonexistent_source_ids:
                raise ValidationError(
                    branchset_node, self.filename, self.basepath,
                    'source ids %s are not defined in source models' % \
                    list(nonexistent_source_ids)
                )
            filters['applyToSources'] = source_ids
        if 'applyToTectonicRegionType' in filters:
            if not filters['applyToTectonicRegionType'] \
                    in self.tectonic_region_types:
                raise ValidationError(
                    branchset_node, self.filename, self.basepath,
                    "source models don't define sources of tectonic region " \
                    "type %r" % filters['applyToTectonicRegionType']
                )
        if 'applyToSourceType' in filters:
            if not filters['applyToSourceType'] in self.source_types:
                raise ValidationError(
                    branchset_node, self.filename, self.basepath,
                    "source models don't define sources of type %r" % \
                    filters['applyToSourceType']
                )

        if not filters:
            filter_ = filter_value = None
        else:
            [[filter_, filter_value]] = filters.items()
        if uncertainty_type in ('abGRAbsolute', 'maxMagGRAbsolute'):
            if not filter_ or not filter_ == 'applyToSources' \
                    or not len(filter_value) == 1:
                raise ValidationError(
                    branchset_node, self.filename, self.basepath,
                    "uncertainty of type %r must define 'applyToSources' " \
                    "with only one source id" % uncertainty_type
                )
        return filters

    def validate_branchset(self, branchset_node, depth, number, branchset):
        if depth == 0:
            if number > 0:
                raise ValidationError(
                    branchset_node, self.filename, self.basepath,
                    'there must be only one branch set ' \
                    'on first branching level'
                )
            elif branchset.uncertainty_type != 'sourceModel':
                raise ValidationError(
                    branchset_node, self.filename, self.basepath,
                    'first branchset must define an uncertainty ' \
                    'of type "sourceModel"'
                )
        else:
            if branchset.uncertainty_type == 'sourceModel':
                raise ValidationError(
                    branchset_node, self.filename, self.basepath,
                    'uncertainty of type "sourceModel" can be defined ' \
                    'on first branchset only'
                )
            elif branchset.uncertainty_type == 'gmpeModel':
                raise ValidationError(
                    branchset_node, self.filename, self.basepath,
                    'uncertainty of type "gmpeModel" is not allowed ' \
                    'in source model logic tree'
                )
        return branchset

    def apply_branchset(self, branchset_node, branchset):
        apply_to_branches = branchset_node.get('applyToBranches')
        if apply_to_branches:
            apply_to_branches = apply_to_branches.split()
            for branch_id in apply_to_branches:
                if not branch_id in self.branches:
                    raise ValidationError(
                        branchset_node, self.filename, self.basepath,
                        'branch %r is not yet defined' % branch_id
                    )
                branch = self.branches[branch_id]
                if branch.child_branchset is not None:
                    raise ValidationError(
                        branchset_node, self.filename, self.basepath,
                        'branch %r already has child branchset' % branch_id
                    )
                if not branch in self.open_ends:
                    raise ValidationError(
                        branchset_node, self.filename, self.basepath,
                        'applyToBranches must reference only branches ' \
                        'from previous branching level'
                    )
                branch.child_branchset = branchset
        else:
            super(SourceModelLogicTree, self).apply_branchset(branchset_node,
                                                              branchset)

    def collect_source_model_data(self, filename):
        all_source_types = set('{%s}%sSource' % (self.NRML, tagname)
                               for tagname in self.SOURCE_TYPES)
        tectonic_region_type_tag = '{%s}tectonicRegion' % self.NRML
        sourcetype_slice = slice(len('{%s}' % self.NRML), - len('Source'))
        eventstream = etree.iterparse(self._open_file(filename),
                                      tag='{%s}*' % self.NRML,
                                      schema=self.get_xmlschema())
        while True:
            try:
                event, node = next(eventstream)
            except StopIteration:
                break
            except etree.XMLSyntaxError as exc:
                raise ParsingError(filename, self.basepath, str(exc))
            if not node.tag in all_source_types:
                if node.tag == tectonic_region_type_tag:
                    self.tectonic_region_types.add(node.text)
            else:
                source_id = node.attrib['{http://www.opengis.net/gml}id']
                source_type = node.tag[sourcetype_slice]
                if source_type == 'point' or source_type == 'area':
                    mfds = len(node.find('{%s}ruptureRateModel' % self.NRML))
                else:
                    mfds = 1
                self.source_ids_to_num_mfds[source_id] = mfds

                self.source_types.add(source_type)

                # saving memory by removing already processed nodes.
                # see http://lxml.de/parsing.html#modifying-the-tree
                node.clear()
                parent = node.getparent()
                prev = node.getprevious()
                while prev is not None:
                    parent.remove(prev)
                    prev = node.getprevious()


class GMPELogicTree(BaseLogicTree):
    GMPEs = frozenset("""\
        Abrahamson_2000_AttenRel
        AS_1997_AttenRel
        AS_2008_AttenRel
        AW_2010_AttenRel
        BA_2008_AttenRel
        BC_2004_AttenRel
        BJF_1997_AttenRel
        BS_2003_AttenRel
        BW_1997_AttenRel
        Campbell_1997_AttenRel
        CB_2003_AttenRel
        CB_2008_AttenRel
        CL_2002_AttenRel
        CS_2005_AttenRel
        CY_2008_AttenRel
        DahleEtAl_1995_AttenRel
        Field_2000_AttenRel
        GouletEtAl_2006_AttenRel
        McVerryetal_2000_AttenRel
        SadighEtAl_1997_AttenRel
        SEA_1999_AttenRel
        WC94_DisplMagRel""".split()
    )

    def __init__(self, tectonic_region_types, *args, **kwargs):
        self.tectonic_region_types = frozenset(tectonic_region_types)
        self.defined_tectonic_region_types = set()
        super(GMPELogicTree, self).__init__(*args, **kwargs)

    def validate_uncertainty_value(self, node, branchset, value):
        if not value in self.GMPEs:
            raise ValidationError(
                node, self.filename, self.basepath,
                'gmpe %r is not available' % value
            )
        return value

    def validate_filters(self, node, uncertainty_type, filters):
        if not filters \
                or len(filters) > 1 \
                or filters.keys() != ['applyToTectonicRegionType']:
            raise ValidationError(
                node, self.filename, self.basepath,
                'branch sets in gmpe logic tree must define only ' \
                '"applyToTectonicRegionType" filter'
            )
        trt = filters['applyToTectonicRegionType']
        if not trt in self.tectonic_region_types:
            raise ValidationError(
                node, self.filename, self.basepath,
                "source models don't define sources of tectonic region " \
                "type %r" % trt
            )
        if trt in self.defined_tectonic_region_types:
            raise ValidationError(
                node, self.filename, self.basepath,
                'gmpe uncertainty for tectonic region type %r has already ' \
                'been defined' % trt
            )
        self.defined_tectonic_region_types.add(trt)
        return filters

    def validate_tree(self, tree_node, root_branchset):
        missing_trts = self.tectonic_region_types \
                       - self.defined_tectonic_region_types
        if missing_trts:
            raise ValidationError(
                tree_node, self.filename, self.basepath,
                'the following tectonic region types are defined ' \
                'in source model logic tree but not in gmpe logic tree: %s' % \
                list(sorted(missing_trts))
            )
        return root_branchset

    def validate_branchset(self, branchset_node, depth, number, branchset):
        if not branchset.uncertainty_type == 'gmpeModel':
            raise ValidationError(
                branchset_node, self.filename, self.basepath,
                'only uncertainties of type "gmpeModel" are allowed ' \
                'in gmpe logic tree'
            )
        if number != 0:
            raise ValidationError(
                branchset_node, self.filename, self.basepath,
                'only one branchset on each branching level is allowed ' \
                'in gmpe logic tree'
            )
        return branchset


class LogicTreeProcessor(object):
    # TODO: unittest
    def __init__(self, basepath, source_model_logictree_path,
                 gmpe_logictree_path):
        self.source_model_lt = SourceModelLogicTree(
            basepath, source_model_logictree_path
        )
        self.gmpe_lt = GMPELogicTree(basepath, gmpe_logictree_path)

    def sample_and_save_source_model_logictree(self, cache, key, random_seed):
        cache.set(key, self.sample_source_model_logictree(random_seed))

    def sample_source_model_logictree(self, random_seed):
        rnd = random.Random(random_seed)
        SourceModelReader = jvm().JClass('org.gem.engine.hazard.' \
                                         'parsers.SourceModelReader')
        branch = self.source_model_lt.root_branchset.sample(rnd)
        sources = SourceModelReader(branch.value).read()

        while True:
            branchset = branch.child_branchset
            if branchset is None:
                break
            branch = branchset.sample(rnd)
            for source in sources:
                branchset.apply_uncertainty(branch.value, source)

        serializer = jvm().JClass('org.gem.JsonSerializer')
        return serializer.getJsonSourceList(sources)
