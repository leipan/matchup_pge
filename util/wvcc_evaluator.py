#!/usr/bin/env python

from builtins import str
import os
import sys
import time
import json
import requests
import logging
import traceback
import shutil
### import backoff

from mycelery import app
### from hysds.dataset_ingest import ingest

### from standard_product_localizer import publish_data, get_acq_object


# set logger
log_format = "[%(asctime)s: %(levelname)s/%(name)s/%(funcName)s] %(message)s"
logging.basicConfig(format=log_format, level=logging.INFO)


class LogFilter(logging.Filter):
    def filter(self, record):
        if not hasattr(record, 'id'):
            record.id = '--'
        return True


logger = logging.getLogger(os.path.splitext(os.path.basename(__file__))[0])
logger.setLevel(logging.INFO)
logger.addFilter(LogFilter())


def test_query(dataset_type):

  query = {"query":{
             "bool":{
               "must":[{"term":{"dataset_type.raw":dataset_type}}]
             }
           },
           "sort":[{"_timestamp":{"order":"desc"}}],
           "fields":["_timestamp","_source"]
          }
  result = query_es(query, '')
  logger.info("result: %s" % result)

              
### @backoff.on_exception(backoff.expo, requests.exceptions.RequestException, max_tries=8, max_value=32)
def query_es(query, idx, url=app.conf['GRQ_ES_URL']):
    """Query ES index."""

    logger.info("url: %s" % url)

    hits = []
    url = url[:-1] if url.endswith('/') else url
    query_url = "{}/{}/_search?search_type=scan&scroll=60&size=100".format(url, idx)
    logger.info("url: {}".format(url))
    logger.info("idx: {}".format(idx))
    logger.info("query: {}".format(json.dumps(query, indent=2)))
    r = requests.post(query_url, data=json.dumps(query))
    r.raise_for_status()
    scan_result = r.json()
    count = scan_result['hits']['total']
    if count == 0: return hits
    scroll_id = scan_result['_scroll_id']
    while True:
        r = requests.post('%s/_search/scroll?scroll=60m' % url, data=scroll_id)
        res = r.json()
        scroll_id = res['_scroll_id']
        if len(res['hits']['hits']) == 0: break
        hits.extend(res['hits']['hits'])
    return hits


def resolve_acq(slc_id, version):
    """Resolve acquisition id."""

    query = {
        "query": {
            "bool": {
                "must": [
                    {"term": {"metadata.identifier.raw": slc_id}},
                    {"term": {"system_version.raw": version}},
                ]
            }
        },
        "fields": [],
    }
    es_index = "grq_{}_acquisition-s1-iw_slc".format(version)
    result = query_es(query, es_index)

    if len(result) == 0:
        logger.info("query : \n%s\n" % query)
        raise RuntimeError("Failed to resolve acquisition for SLC ID: {} and version: {}".format(slc_id, version))

    return result[0]['_id']


def ifgcfg_exists(ifgcfg_id, version):
    """Return True if ifg-cfg exists."""

    query = {
        "query": {
            "ids": {
                "values": [ifgcfg_id],
            }
        },
        "fields": []
    }
    index = "grq_{}_s1-gunw-ifg-cfg".format(version)
    result = query_es(query, index)
    return False if len(result) == 0 else True




def main():
    """Main."""

    # read in context
    context_file = os.path.abspath("_context.json")
    if not os.path.exists(context_file):
        raise RuntimeError
    with open(context_file) as f:
        ctx = json.load(f)

    # resolve acquisition id from slc id
    ### slc_id = ctx['slc_id']
    ### slc_version = ctx['slc_version']
    ### acq_version = ctx['acquisition_version']
    ### logger.info("slc_id : %s" % slc_id)
    ### acq_id = resolve_acq(slc_id, acq_version)
    ### acq_id = resolve_acq()
    ### logger.info("acq_id: {}".format(acq_id))

    dataset_type = "CRIS-data"
    test_query(dataset_type)
    dataset_type = "VNP03MOD-data"
    test_query(dataset_type)



if __name__ == "__main__":
    try:
        status = main()
    except (Exception, SystemExit) as e:
        with open('_alt_error.txt', 'w') as f:
            f.write("%s\n" % str(e))
        with open('_alt_traceback.txt', 'w') as f:
            f.write("%s\n" % traceback.format_exc())
        raise
    sys.exit(status)
