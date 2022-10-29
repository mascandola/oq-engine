# -*- coding: utf-8 -*-
# vim: tabstop=4 shiftwidth=4 softtabstop=4
#
# Copyright (C) 2022, GEM Foundation
#
# OpenQuake is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# OpenQuake is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with OpenQuake.  If not, see <http://www.gnu.org/licenses/>.

import os
import time
import unittest
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from openquake.hazardlib import valid
from openquake.baselib.general import DictArray
from openquake.hazardlib.calc.mrd import (
    update_mrd, update_mrd_indirect, get_uneven_bins_edges)
from openquake.hazardlib.contexts import ContextMaker
from openquake.commonlib.datastore import read as dstore_read
from openquake.hazardlib.cross_correlation import BakerJayaram2008

PLOT = False
LOG = True
CWD = os.path.dirname(__file__)


class MRD01TestCase(unittest.TestCase):
    """ Computes the mean rate density using a simple PSHA input model """

    def setUp(self):

        # Settings
        fname = os.path.join(CWD, 'data', 'mrd', 'calc_934.hdf5')
        self.imts = ['SA(0.2)', 'SA(1.0)']

        # Load datastore
        self.dstore = dstore_read(fname)
        self.oqp = self.dstore['oqparam']

        # Only 1 TRT in this example
        gmm_lt = self.dstore.read_df('full_lt/gsim_lt')
        trts = list(pd.unique(gmm_lt.trt))
        trt = trts[0].decode('UTF-8')

        # Define the GMM
        tmps = gmm_lt.iloc[0].uncertainty.decode('UTF-8')
        gmm = valid.gsim(tmps)

        # Create the context maker and set the IMTLS
        self.cmaker = ContextMaker(trt, [gmm], self.oqp)
        self.cmaker.grp_id = 0
        self.cmaker.imtls = \
            DictArray({k: self.oqp.imtls[k] for k in self.imts})

        # Read contexts
        self.ctxs = self.cmaker.read_ctxs(self.dstore)

        # Set the cross correlation model
        self.crosscorr = BakerJayaram2008()

    def test_direct(self):
        """ test the direct method """

        if LOG:
            start_time = time.time()

        # Compute the MRD
        imls1 = self.oqp.hazard_imtls[self.imts[0]]
        imls2 = self.oqp.hazard_imtls[self.imts[1]]
        len1 = len(imls1)-1
        len2 = len(imls2)-1
        nsites = len(self.oqp.sites)
        mrd = np.zeros((len1, len2, nsites, len(self.cmaker.gsims)))
        update_mrd(self.ctxs, self.cmaker, self.crosscorr, mrd)

        # Loading Hazard Curves.
        # The poes array is 4D: |sites| x || x |IMTs| x |IMLs|
        poes = self.dstore['hcurves-stats'][:]
        afe = - np.log(1-poes)
        afo = afe[:, :, :, :-1] - afe[:, :, :, 1:]

        imts = list(self.oqp.hazard_imtls)
        idx1 = imts.index(self.imts[0])
        idx2 = imts.index(self.imts[1])

        afo1 = afo[0, 0, idx1, :]
        afo2 = afo[0, 0, idx2, :]

        tmp = self.oqp.hazard_imtls[self.imts[0]]
        c1 = tmp[:-1] + np.diff(tmp) / 2
        tmp = self.oqp.hazard_imtls[self.imts[1]]
        c2 = tmp[:-1] + np.diff(tmp) / 2

        # Compute marginal
        cm1 = imls1[:-1] + np.diff(imls1) / 2
        marg1 = np.squeeze(np.sum(mrd, axis=0))
        cm2 = imls2[:-1] + np.diff(imls2) / 2
        marg2 = np.squeeze(np.sum(mrd, axis=1))

        # Test
        np.testing.assert_almost_equal(marg1, afo1, decimal=5)
        np.testing.assert_almost_equal(marg2, afo2, decimal=5)

        if PLOT:
            plt.title('Direct method test')
            plt.plot(c1, afo1, label=f'HC {self.imts[0]}')
            plt.plot(cm1, marg1, 'o', mfc='None')
            plt.plot(c2, afo2, label=f'HC {self.imts[1]}')
            plt.plot(cm2, marg2, 'o', mfc='None', )
            plt.xlabel('Spectral Acceleration, S$_a$ [g]')
            plt.ylabel('Annual Rate of Occurrence')
            plt.legend()
            plt.grid(which='minor', ls=':', color='lightgrey')
            plt.grid(which='major', ls='--', color='grey')
            plt.show()

        if LOG:
            print(f"--- {time.time() - start_time} seconds ---")

    def test_indirect(self):
        """ test the indirect method """

        if LOG:
            start_time = time.time()

        # Bin edges
        lefts = [-3, -2, 1, 2]
        numb = [80, 80, 10]
        be_mea = get_uneven_bins_edges(lefts, numb)
        be_sig = np.arange(0.50, 0.70, 0.01)

        # Compute the MRD
        imls1 = self.oqp.hazard_imtls[self.imts[0]]
        imls2 = self.oqp.hazard_imtls[self.imts[1]]
        len1 = len(imls1)-1
        len2 = len(imls2)-1
        nsites = len(self.oqp.sites)
        mrd = np.zeros((len1, len2, nsites, len(self.cmaker.gsims)))
        update_mrd_indirect(
            self.ctxs, self.cmaker, self.crosscorr, mrd, be_mea, be_sig)

        # Loading Hazard Curves.
        # The poes array is 4D: |sites| x || x |IMTs| x |IMLs|
        poes = self.dstore['hcurves-stats'][:]
        afe = - np.log(1-poes)
        afo = afe[:, :, :, :-1] - afe[:, :, :, 1:]

        imts = list(self.oqp.hazard_imtls)
        idx1 = imts.index(self.imts[0])
        idx2 = imts.index(self.imts[1])

        afo1 = afo[0, 0, idx1, :]
        afo2 = afo[0, 0, idx2, :]

        tmp = self.oqp.hazard_imtls[self.imts[0]]
        c1 = tmp[:-1] + np.diff(tmp) / 2
        tmp = self.oqp.hazard_imtls[self.imts[1]]
        c2 = tmp[:-1] + np.diff(tmp) / 2

        # Compute marginal
        cm1 = imls1[:-1] + np.diff(imls1) / 2
        marg1 = np.squeeze(np.sum(mrd, axis=0))
        cm2 = imls2[:-1] + np.diff(imls2) / 2
        marg2 = np.squeeze(np.sum(mrd, axis=1))

        # Test
        np.testing.assert_almost_equal(marg1, afo1, decimal=5)
        np.testing.assert_almost_equal(marg2, afo2, decimal=5)

        if PLOT:
            plt.title('Indirect method test')
            plt.plot(c1, afo1, label=f'HC {self.imts[0]}')
            plt.plot(cm1, marg1, 'o', mfc='None')
            plt.plot(c2, afo2, label=f'HC {self.imts[1]}')
            plt.plot(cm2, marg2, 'o', mfc='None', )
            plt.xlabel('Spectral Acceleration, S$_a$ [g]')
            plt.ylabel('Annual Rate of Occurrence')
            plt.legend()
            plt.grid(which='minor', ls=':', color='lightgrey')
            plt.grid(which='major', ls='--', color='grey')
            plt.show()

        if LOG:
            print(f"--- {time.time() - start_time} seconds ---")
