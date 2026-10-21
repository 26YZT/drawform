"""Safe relative paths shared by old sessions and immutable execution attempts."""
import re


def _attempt(data,key,legacy,suffix):
    if key not in data:return legacy
    identity=data[key]
    if not isinstance(identity,str) or not re.fullmatch('[0-9a-f]{32}',identity):
        raise ValueError('执行尝试指针无效。')
    return 'attempts/'+identity+'/'+suffix


def model_relative(data):return _attempt(data,'model_attempt_id','artifacts','artifacts')
def manifest_relative(data):return _attempt(data,'model_attempt_id','manifest.json','manifest.json')
def analysis_relative(data):return _attempt(data,'analysis_attempt_id','analysis','analysis')
