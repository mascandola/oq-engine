""" Tokens for kvs keys """
import openquake.kvs

# hazard tokens
SOURCE_MODEL_TOKEN = 'sources'
GMPE_TOKEN = 'gmpe'
JOB_TOKEN = 'job'
ERF_KEY_TOKEN = 'erf'
MGM_KEY_TOKEN = 'mgm'
HAZARD_CURVE_KEY_TOKEN = 'hazard_curve'
STOCHASTIC_SET_TOKEN = 'ses'

# risk tokens
CONDITIONAL_LOSS_KEY_TOKEN = 'LOSS_AT_'
EXPOSURE_KEY_TOKEN = 'ASSET'
GMF_KEY_TOKEN = 'GMF'
LOSS_RATIO_CURVE_KEY_TOKEN = 'LOSS_RATIO_CURVE'
LOSS_CURVE_KEY_TOKEN = 'LOSS_CURVE'

def loss_token(poe):
    """ Return a loss token made up of the CONDITIONAL_LOSS_KEY_TOKEN and 
    the poe cast to a string """
    return "%s%s" % (CONDITIONAL_LOSS_KEY_TOKEN, str(poe))

def vuln_key(job_id):
    """Generate the key used to store vulnerability curves."""
    return openquake.kvs.generate_product_key(job_id, "VULN_CURVES")

def asset_key(job_id, row, col):
    """ Return an asset key generated by openquake.kvs._generate_key """
    return openquake.kvs.generate_key([job_id, row, col,
        EXPOSURE_KEY_TOKEN])

def loss_ratio_key(job_id, row, col, asset_id):
    """ Return a loss ratio key generated by openquake.kvs.generate_key """
    return openquake.kvs.generate_key([job_id, row, col,
        LOSS_RATIO_CURVE_KEY_TOKEN, asset_id])

def loss_curve_key(job_id, row, col, asset_id):
    """ Return a loss curve key generated by openquake.kvs.generate_key """
    return openquake.kvs.generate_key([job_id, row, col, 
        LOSS_CURVE_KEY_TOKEN, asset_id])

def loss_key(job_id, row, col, asset_id, poe):
    """ Return a loss key generated by openquake.kvs.generate_key """
    return openquake.kvs.generate_key([job_id, row, col, loss_token(poe), 
        asset_id])

def hazard_curve_key(job_id, realization_num, site_lon, site_lat):
    """ Result a hazard curve key (for a single site) generated by
    openquake.kvs.generate_key """
    return openquake.kvs.generate_key([HAZARD_CURVE_KEY_TOKEN,
                                       job_id,
                                       realization_num, 
                                       site_lon, 
                                       site_lat])
