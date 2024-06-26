import collections
import json
import re
#
import idaapi
import idautils
#
from idaclu import ida_shims
from idaclu.qt_utils import i18n
#
import helpers


SCRIPT_NAME = i18n('Orphan Blocks')
SCRIPT_TYPE = 'func'
SCRIPT_VIEW = 'tree'
SCRIPT_ARGS = []


def sort_nat(input_dict):
    def fun(k, v):
        return [k, int(v)]

    def cmp_key(t):
        return fun(*re.match(r'([a-zA-Z\: ]+)(-?\d+)', t[0]).groups())

    return collections.OrderedDict(sorted(input_dict.items(), key=cmp_key, reverse=True))

def get_data(func_gen=None, env_desc=None, plug_params=None):

    report = {
        'data': collections.defaultdict(list),
        'stat': collections.defaultdict(int)
    }

    for func_addr in func_gen():
        orphs_num = helpers.get_orph_count(func_addr)
        
        orph_key = "blocks: {}".format(orphs_num)
        
        report['data'][orph_key].append(func_addr)
        report['stat'][orph_key] += 1
        
    report['data'] = sort_nat(report['data'])
    report['stat'] = sort_nat(report['stat'])

    return report if __name__ == '__main__' else report['data']


def debug():
    data_obj = get_data(func_gen=idautils.Functions)
    ida_shims.msg(json.dumps(data_obj, indent=4))

if __name__ == '__main__':
    debug()
