"""Evidence-backed study relevance. Keywords alone never establish exam importance."""
import re
from typing import Any
from app.editorial import document,select_document
from app.models import News,LearningMaterial

METHOD = 'exam-relevance-2'

PROFILES={'general':'综合阅读','postgrad':'考研政治','civil':'公考申论／面试'}
COMMON={
 'major_meeting':('重大会议与文件',r'全国两会|全国人民代表大会|中央全会|中央经济工作会议|中央一号文件|政府工作报告|党的.*全国代表大会|五中全会|四中全会|三中全会|二十届|APEC|亚太经合组织|二十国集团|金砖国家领导人|联合国大会'),
 'framework':('政策主线与理论框架',r'中国式现代化|新质生产力|十五五|数字经济|全面深化改革'),
}
POSTGRAD={**COMMON,
 'speech':('重要讲话与纪念活动',r'新年贺词|七一讲话|重要讲话|重要指示|重要批示|重要文章|纪念.{0,30}(?:周年|胜利)|诞辰\d+周年'),
 'science':('重大科技进展',r'神舟|嫦娥|天宫|空间站|量子|大模型|人工智能'),
}
CIVIL={**COMMON,
 'governance':('基层治理与三农',r'乡村振兴|千万工程|基层治理|基层减负|返乡创业|三农|公共服务|社区治理|农村治污|农村污水|网络暴力|网暴|城市治理'),
 'livelihood':('民生与公共服务',r'就业|养老|银发经济|体重管理|医保|教育改革|教育公平|大学.{0,8}(?:教育|教学)|社会保障|托育'),
 'industry':('产业与绿色转型',r'人工智能\+|人工智能|低空经济|设备更新|绿色低碳|生态保护|生态法治|数字化转型|交通运输|物流成本|基础设施|区域协调'),
 'institution':('政策制度与规范',r'条例|实施方案|管理办法|政府工作报告|法治|制度改革|司法制度|党内法规|从严治党|潜规则'),
}
ACTION=re.compile(r'印发|发布|公布|实施|施行|审议|提出|部署|明确|建立|完善|突破|成功|发射|纪念|举行|讲话|指出|强调')
NOISE=re.compile(r'娱乐八卦|明星绯闻|赛事比分|球赛战报|妆造|化妆师|汉服馆|游玩攻略')


def assess(row: News | LearningMaterial,profile: str,overrides: dict | None=None,capture: dict | None=None) -> dict[str,Any]:
    selection=select_document(row,overrides)
    data=document(row)
    result:dict[str,Any]={'method':METHOD,'eligible':False,'score':0,'topic':None,'label':None,'evidence':[],
                          'reason':'未满足本方向的明确主题与具体信息要求',
                          'reading_focus':[]}
    if not selection.selected:
        result['reason']='未通过基础时政筛选';return result
    if selection.category not in {'policy','report','analysis'}:
        result['reason']='内容类别尚未明确为政策、评论或报道';return result
    if capture and capture.get('status')!='extracted':
        result['reason']='正文范围待核对，暂不进入方向简报';return result
    if NOISE.search(data['title']):
        result['reason']='属于生活消费、娱乐或赛事类内容';return result
    if profile=='postgrad' and re.search(r'^(?:山东|天津|北京|上海|广东|江苏|浙江|福建|四川|河北|河南|湖北|湖南|安徽|江西|辽宁|吉林|黑龙江|广西|贵州|云南|海南|重庆|陕西|山西|甘肃|青海|宁夏|内蒙古|新疆|西藏).{0,8}(?:发布|出台|印发|制定)',data['title']):
        result['reason']='地方政策，保留供查阅，暂不作为考研方向的每日重点';return result
    patterns=POSTGRAD if profile=='postgrad' else CIVIL
    hits=[]
    for key,(label,pattern) in patterns.items():
        title_hit=re.search(pattern,data['title'])
        body_hit=re.search(pattern,data['text'][:1000])
        # Civil commentary can express its public issue in the lead. Postgrad
        # reports also admit explicit national speeches, never generic AI chatter.
        body_allowed = (selection.category=='policy' or
                        profile=='civil' and selection.category=='analysis' and
                        bool(re.search(r'公共|基层|农村|民生|就业|养老|医保|法治|污染|治污|减负|教育|大学|高校|(?<!全球)(?<!政)治理|从严治党|法规|社会保障|物流成本|基础设施|区域协调',data['text'][:650])) and
                        bool(re.search(r'治理|整治|应当|必须|不能|需要|改革|制度|建议|破除',data['text'][:1000])) or
                        profile=='postgrad' and selection.category=='report' and key in {'major_meeting','speech'} and
                        bool(re.search(r'习近平|国务院|中共中央',data['text'][:300])) or
                        profile=='civil' and selection.category=='report' and key=='institution' and
                        bool(re.search(r'重要指示|重要批示',data['title'])) or
                        profile=='postgrad' and selection.category=='analysis' and key=='major_meeting' and
                        bool(re.search(r'党的二十届.{0,8}全会|亚太经合组织.{0,30}领导人.{0,8}会议',data['text'][:300])) and
                        bool(re.search(r'审议通过|部署|举行|坚持|推动',data['text'][:650])))
        if not title_hit and not (body_hit and body_allowed):continue
        hit=title_hit or body_hit;field='title' if title_hit else 'text';text=data[field]
        if hit is None:continue
        hits.append((key,label,{'field':field,'quote':text[max(0,hit.start()-18):hit.end()+60]}))
    if not hits:return result
    action=ACTION.search(data['text'][:1600])
    if not action and profile=='civil' and selection.category=='analysis':
        action=re.search(r'治理|整治|纠偏|应当|必须|不能|需要|破除',data['text'][:1000])
    if not action:
        result['reason']='只有主题词，未识别到具体政策、行动或新信息';return result
    key,label,proof=hits[0]
    if profile=='civil' and selection.category=='report' and key!='major_meeting':
        # Attendance or an exchange meeting alone is not an implemented policy
        # or a usable governance case. Negated announcements are not progress.
        concrete=data['title']+' '+data['text'][:1000]
        measures=[m for m in re.finditer(r'印发|发布|公布|实施|施行|审议|提出|部署|明确|建立|完善|启动|整治|治理|突破|成功|发射|指示|批示|研发',concrete)
                  if not re.search(r'尚未|未能|没有|未取得|未实现|未公布|缺少',concrete[max(0,m.start()-12):m.start()])]
        if not measures:
            result['reason']='尚无具体政策举措或可用治理案例，仅有话题或交流活动';return result
    if profile=='postgrad' and key=='science':
        science_text=data['title']+' '+data['text'][:500]
        achievements=[m for m in re.finditer(r'突破|成功|首次|首个|发射|研制|建成|研发|成果',science_text)
                      if not re.search(r'尚未|未能|没有|未取得|未实现|未公布|缺少',science_text[max(0,m.start()-12):m.start()])]
        if not achievements:
            result['reason']='科技主题缺少明确成果或突破信息';return result
    base={'policy':300,'analysis':200,'report':100}[selection.category]
    result.update(eligible=True,score=base+(8 if key=='major_meeting' else 0),topic=key,label=label,
                  evidence=[proof,{'field':'text','quote':data['text'][max(0,action.start()-18):action.end()+80]}],
                  reason=f'{label}，原文包含明确政策或行动；'+{'policy':'政策文件优先','analysis':'评论观点需与事实区分','report':'按公开报道阅读'}[selection.category],
                  reading_focus=['重要部署与新表述','联系已学理论框架','核对会议与纪念背景'] if profile=='postgrad'
                  else ['问题与政策措施','适用对象及关键数据','可积累的规范表述与案例'])
    return result
