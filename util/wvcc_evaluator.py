#!/usr/bin/env python

from builtins import str
import os
import sys
import time
import datetime
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
               ### "must":[{"term":{"dataset_type.raw":dataset_type}}],
               ### "must":[{"term":{"id.raw":"VNP03MOD.A2015152.0600.001.2017261064559"}}]
               ### "must":[{"term":{"dataset_type.raw":dataset_type}}, {"term":{"starttime":"2015-06-01T06:00:00.000Z"}}, {"term":{"endtime":"2015-06-01T06:06:00.000Z"}}]
               ### "must":[{"term":{"dataset_type.raw":dataset_type}}, {"range":{"starttime":{"lte":"2015-06-01T00:55:00.000Z"}}}]
               "must":[{"term":{"dataset_type.raw":dataset_type}}, {"range":{"starttime":{"lte":"2015-06-01T20:55:00.000Z"}}}]
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
    ### logger.info("query: {}".format(json.dumps(query, indent=2)))
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


def query_range(dataset_type, str_start_time, str_end_time):
  query = {"query":{
             "bool":{
               "must":[{"term":{"dataset_type.raw":dataset_type}}, {"range":{"starttime":{"gte":str_start_time}}}, {"range":{"endtime":{"lte":str_end_time}}}]
             }
           },
           "sort":[{"_timestamp":{"order":"desc"}}],
           "fields":["_timestamp","_source"]
          }
  result = query_es(query, '')

  return result


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


def cris_viirs_cfg(start_time, end_time):
  # for each day worth of CrIS granules,
  # find all the corresponding VIIRS granules in that same day
  # plus one additional VIIRS granule on either side of the day boundary
  # to form cris_viirs_cfg, and publish it to GRQ

  config_cris_viirs = {}


  # --- sounder CrIS

  str_start_time = start_time.strftime('%Y-%m-%dT%H:%M:%SZ')
  logger.info("str_start_time: {}".format(str_start_time))
  str_end_time = end_time.strftime('%Y-%m-%dT%H:%M:%SZ')
  logger.info("str_end_time: {}".format(str_end_time))

  # get all CrIS granules between start_time and end_time
  dataset_type = 'CRIS-data'
  result = query_range(dataset_type, str_start_time, str_end_time)

  ### logger.info("result: {}".format(json.dumps(result, indent=2)))
  logger.info("len(result): %s" % str(len(result)))
  logger.info("")

  # collect all sounder granules in a list
  logger.info("----------- sounder granule urls: ----------")
  sounder_urls = query_result_2_url_list(result)

  config_cris_viirs['sounder_granule_urls'] = sounder_urls


  # --- imager VIIRS

  imager_urls = []

  # get all VIIRS granules between start_time and end_time
  dataset_type = 'VNP03MOD-data'
  result = query_range(dataset_type, str_start_time, str_end_time)
  logger.info("")
  ### logger.info("result: %s" % result)
  ### logger.info("VIIRS result: {}".format(json.dumps(result, indent=2)))
  logger.info("len(result): %s" % str(len(result)))
  logger.info("")

  # collect all imager granules in a list
  logger.info("----------- imager granule urls: ----------")
  vlist0 = query_result_2_url_list(result)

  # get two VIIRS granules on the boundary
  str_start_time1 = (start_time-datetime.timedelta(minutes=12)).strftime('%Y-%m-%dT%H:%M:%SZ')
  result = query_range(dataset_type, str_start_time1, str_start_time)
  logger.info("----------- imager granule left boundary urls: ----------")
  vlist1 = query_result_2_url_list(result)

  str_end_time1 = (end_time+datetime.timedelta(minutes=12)).strftime('%Y-%m-%dT%H:%M:%SZ')
  result = query_range(dataset_type, str_end_time, str_end_time1)
  logger.info("----------- imager granule right boundary urls: ----------")
  vlist2 = query_result_2_url_list(result)

  imager_urls += vlist1
  imager_urls += vlist0
  imager_urls += vlist2

  config_cris_viirs['imager_granule_urls'] = imager_urls

  logger.info("config_cris_viirs: {}".format(json.dumps(config_cris_viirs, indent=2)))



  # publish to GRQ/ES




def query_result_2_url_list(result):
  list1 = []
  for item in result:
    list1.append(item['_source']['urls'])

  logger.info("item urls: {}".format(json.dumps(list1, indent=2)))
  return list1



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
    ### test_query(dataset_type)

    dataset_type = "VNP03MOD-data"
    ### test_query(dataset_type)

    start_time = datetime.datetime(2015, 06, 01, 20, 15, 00, 000)
    end_time = datetime.datetime(2015, 06, 01, 20, 55, 00, 000)

    ### cris_viirs_cfg("2015-06-01T20:15:00.000Z", "2015-06-01T20:55:00.000Z")
    cris_viirs_cfg(start_time, end_time)

    ### cris_viirs_cfg('2015-06-01T10:45:00Z', '2015-06-25T20:55:00Z')

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
