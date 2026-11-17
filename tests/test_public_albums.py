import json
import pytest
from app.public_albums import article_identity,parse_directory,linked_articles


def test_html_and_json_directory_pages_match_without_running_scripts():
    url='https://mp.weixin.qq.com/s?__biz=abc&mid=123&idx=1'
    raw=f"article_count: '1', articleList: [{{url: '{url.replace('&','&amp;')}', msgid: '123', itemidx: '1'}}]"
    html=parse_directory(raw)
    obj=parse_directory(json.dumps({'base_resp':{'ret':0},'getalbum_resp':{'article_count':1,'article_list':[{'url':url,'msgid':123,'itemidx':1}],'continue_flag':0}}))
    assert html['items']==obj['items'] and html['declared']==1
    assert obj['more'] is False


def test_reject_error_pages_and_offsite_urls_and_deduplicate_identity():
    for raw in ['环境异常，请去验证','{"base_resp":{"ret":-3}}']:
        with pytest.raises(ValueError):parse_directory(raw)
    with pytest.raises(ValueError):article_identity('https://evil.test/s?__biz=abc&mid=1&idx=1')
    first='https://mp.weixin.qq.com/s?__biz=abc&mid=1&idx=1&sn=old'
    second=first.replace('sn=old','sn=new')
    assert article_identity(first)==article_identity(second)
    obj={'base_resp':{'ret':0},'getalbum_resp':{'article_list':[{'url':first},{'url':second}]}}
    assert len(parse_directory(json.dumps(obj))['items'])==1


def test_body_references_stay_in_verified_account_and_deduplicate():
    good='https://mp.weixin.qq.com/s?__biz=abc&amp;mid=123&amp;idx=1'
    raw=f'<a href="{good}&amp;mid=999">outside body</a><div id="js_content"><a href="{good}">民生政策</a><a href="{good}&amp;sn=updated">同一篇</a><a href="https://mp.weixin.qq.com/s?__biz=other&amp;mid=3&amp;idx=1">other</a><a href="https://evil.test/s?__biz=abc&amp;mid=2&amp;idx=1">offsite</a></div>'
    items=linked_articles(raw,'abc')
    assert len(items)==1 and items[0]['identity']=='abc:123:1'
    assert items[0]['create_time']==''  # Linked publication time must be fetched, not inferred.
