"""Per-slide and structural section decomposition and batch-merge helpers for large documents."""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from enum import Enum
from dual_pipeline.document_pipeline_core.format_source_units import SourceUnit


class UnitDispositionClass(str, Enum):
    """14-Class Unit Materiality & Quarantine Sieve (C1-C5 promoted/context, CONTEXT_NARRATIVE_OR_RECAP, Q1-Q8 quarantined)."""
    C1_CORE_PORTFOLIO_OR_POD_MATRIX = "CORE_PORTFOLIO_OR_POD_MATRIX"
    C2_CORE_ORG_ROSTER_AND_VISUAL_TREE = "CORE_ORG_ROSTER_AND_VISUAL_TREE"
    C3_CORE_COMMERCIAL_DEAL_SOW_AND_STAFFING = "CORE_COMMERCIAL_DEAL_SOW_AND_STAFFING"
    C4_CORE_ARCHITECTURE_ROADMAP_POLICY = "CORE_ARCHITECTURE_ROADMAP_POLICY"
    C5_TACTICAL_UI_OR_PROCESS_CADENCE = "TACTICAL_UI_OR_PROCESS_CADENCE"
    CONTEXT_NARRATIVE_OR_RECAP = "CONTEXT_NARRATIVE_OR_RECAP"
    Q1_QUARANTINE_TEMPLATE_SCAFFOLD = "QUARANTINE_TEMPLATE_SCAFFOLD"
    Q2_QUARANTINE_UNMODIFIED_TEMPLATE_DEFAULT = "QUARANTINE_UNMODIFIED_TEMPLATE_DEFAULT"
    Q3_QUARANTINE_WORKBOOK_EMPTY_AND_LOOKUP_GRIDS = "QUARANTINE_WORKBOOK_EMPTY_AND_LOOKUP_GRIDS"
    Q4_QUARANTINE_ILLUSTRATIVE_MOCKUP = "QUARANTINE_ILLUSTRATIVE_MOCKUP"
    Q5_QUARANTINE_LEGAL_BOILERPLATE = "QUARANTINE_LEGAL_BOILERPLATE"
    Q6_QUARANTINE_PEDAGOGICAL_AND_PARABLES = "QUARANTINE_PEDAGOGICAL_AND_PARABLES"
    Q7_QUARANTINE_LOGISTICS_DIVIDER_SOCIAL = "QUARANTINE_LOGISTICS_DIVIDER_SOCIAL"
    Q8_QUARANTINE_VENDOR_BOILERPLATE = "QUARANTINE_VENDOR_BOILERPLATE"


CORE_UNIT_CLASSES = frozenset({
    UnitDispositionClass.C1_CORE_PORTFOLIO_OR_POD_MATRIX.value,
    UnitDispositionClass.C2_CORE_ORG_ROSTER_AND_VISUAL_TREE.value,
    UnitDispositionClass.C3_CORE_COMMERCIAL_DEAL_SOW_AND_STAFFING.value,
    UnitDispositionClass.C4_CORE_ARCHITECTURE_ROADMAP_POLICY.value,
})

QUARANTINED_UNIT_CLASSES = frozenset({
    UnitDispositionClass.Q1_QUARANTINE_TEMPLATE_SCAFFOLD.value,
    UnitDispositionClass.Q2_QUARANTINE_UNMODIFIED_TEMPLATE_DEFAULT.value,
    UnitDispositionClass.Q3_QUARANTINE_WORKBOOK_EMPTY_AND_LOOKUP_GRIDS.value,
    UnitDispositionClass.Q4_QUARANTINE_ILLUSTRATIVE_MOCKUP.value,
    UnitDispositionClass.Q5_QUARANTINE_LEGAL_BOILERPLATE.value,
    UnitDispositionClass.Q6_QUARANTINE_PEDAGOGICAL_AND_PARABLES.value,
    UnitDispositionClass.Q7_QUARANTINE_LOGISTICS_DIVIDER_SOCIAL.value,
    UnitDispositionClass.Q8_QUARANTINE_VENDOR_BOILERPLATE.value,
})

_SLIDE_OR_PAGE_HEADING_RE = re.compile(
    r"^(#{1,4})\s*(?:Slide|Page)\s+(\d+)(?:\s*[:\-\u2013\u2014.]\s*(.*))?$",
    re.IGNORECASE,
)
_SUMMARY_HEADING_RE = re.compile(
    r"\b(?:executive\s+overview|executive\s+summary|document\s+overview|table\s+of\s+contents)\b",
    re.IGNORECASE,
)
_NON_SUBSTANTIVE_TITLE_RE = re.compile(
    r"^(?:thank\s+you!?|questions\??|q\s*&\s*a|appendix|agenda|table\s+of\s+contents|divider|section\s+break|break!?|lunch!?|wrap\s*-?\s*up)$",
    re.IGNORECASE,
)

_MASTER_FOOTER_STRIP_RE = re.compile(
    r"(?:©\s*(?:20\d\d)?\s*(?:PartnerAdvisory|Apex Global(?:\s*Analytics)?)[^.\n]*\.?\s*(?:All\s+Rights\s+Reserved\.?)?\s*(?:Proprietary\s+and\s+Confidential\.?)?\s*(?:•\s*Report\s+Issue)?)|"
    r"(?:(?:PartnerAdvisory\.\s*)?All\s+Rights\s+Reserved\.?\s*(?:Proprietary\s+and\s+Confidential\.?)?)|"
    r"(?:\b(?:DRAFT|CONFIDENTIAL)\s+WATERMARK\b)",
    re.IGNORECASE,
)

_Q1_TEMPLATE_SCAFFOLD_RE = re.compile(
    r"(?:read\s+me\s+then\s+delete|\[account\s+name\]|\[account\]|lorem\s+ipsum|"
    r"insert\s+client\s+name|insert\s+opp\s+name|example\s+travel\s+build-up|"
    r"reduce\s+cost\s+per\s+claim\s*(?:-15%\s+on\s+aws)?|\bcomcast\b.*power\s*bi|"
    r"questions\s+to\s+consider\s+as\s+you\s+approach\s+account\s+planning|"
    r"resources\s+to\s+enable\s+planning|1\.\s*capture\s+customer\s+priorities\s*&\s*decision-making|"
    r"\[link:\s*solutions\s+catalog\s*\]|tie\s+partners\s+and\s+competitors\s+to\s+specific\s+account\s+moves|"
    r"stakeholder\s+landscape\s+2027\s+account\s+planning|decision\s*&\s*competitor\s+landscape\s+2027\s+account\s+planning)",
    re.IGNORECASE | re.DOTALL,
)
_Q2_UNMODIFIED_DEFAULT_RE = re.compile(
    r"(?:avg\.?\s*bill\s*rate\s*\(\$230/\$235\)\s*and\s*margin\s*\(48%/50%\)\s*only\s*template\s*default)",
    re.IGNORECASE,
)
_Q3_WORKBOOK_EMPTY_OR_LOOKUP_RE = re.compile(
    r"(?:\btbd(?:19|[2-9]\d|100)\b.*total\s+hours\s*[:=|]\s*0\b|"
    r"do\s+not\s+delete\s+this\s+column|l2/l3/l4\s+helper|"
    r"static\s+country\s+rate\s+lookup\s+grid)",
    re.IGNORECASE,
)
_Q4_ILLUSTRATIVE_MOCKUP_RE = re.compile(
    r"(?:\billustrative\b.*(?:fictional|maria\s+delgado|ev553|experience\s+demo)|"
    r"all\s+applications,\s*neecodes,\s*people,\s*and\s+numbers\s+are\s+fictional|"
    r"\bmaria\s+delgado\b.*\bev553\b)",
    re.IGNORECASE | re.DOTALL,
)
_Q5_LEGAL_BOILERPLATE_RE = re.compile(
    r"\b(?:clayton\s+act|sherman\s+act|securities\s+and\s+exchange\s+commission|"
    r"indemnification\s+and\s+hold\s+harmless|survival\s+of\s+representations\s+and\s+warranties|"
    r"notice\s+and\s+acknowledgement\s+of\s+ongoing|covenants\s+not\s+to\s+compete|"
    r"noncompete\s+period|nonsolicitation\s+period|uniform\s+trade\s+secrets\s+act|"
    r"defend\s+trade\s+secrets\s+act|works?\s+made\s+for\s+hire|assign\s+to\s+partner_advisory\s+all\s+my\s+right|"
    r"mutual\s+arbitration\s+agreement|federal\s+arbitration\s+act|jams\s+employment\s+rules|"
    r"arbitration\s+shall\s+be\s+the\s+sole|voluntary\s+nature\s+of\s+agreement|state\s+specific\s+provisions|"
    r"addendum\s+\d+\s+to\s+employment\s+agreement|(?:fiduciary|\ufb01duciary)\s+duty\s+of\s+loyalty|"
    r"devote\s+all\s+of\s+my\s+business\s+time,\s*energy,\s*and\s+skill|"
    r"compliance\s+with\s+other\s+agreements|indemnify\s+partner_advisory\s+from\s+and\s+against|"
    r"for\s+employees\s+to\s+whom\s+[a-z\s]+\s+laws\s+apply|reasonable\s+restrictions;\s*modification|"
    r"rcw\s+49\.62)\b",
    re.IGNORECASE,
)
_Q6_PEDAGOGICAL_PARABLES_RE = re.compile(
    "|".join(
        (
            r"discussion\s*\(document\)|let[\u2019']?s\s+play\s+a\s+game|game\s*#\d+|freaky\s+friday",
            r"activity:\s*draft|stand\s*&\s*deliver|roleplay\s+exercise|roger\s+bannister",
            r"maya\s+angelou|robin\s+dunbar|dunbar[\u2019']?s\s+number|how\s+does\s+dunbar\s+translate",
            r"what\s+can\s+you\s+do\s+with\s+50\s+relationships|active\s+relationships\s+are\s+important\s+too",
            r"steven\s+covey|netflix\s+horizons|horizon\s+(?:one|two|three)\s*[\-\u2013\u2014]\s*an\s+example",
            r"remember\s+our\s+game\?|always\s+have\s+an\s+intent\s+to\s+help|be\s+curious\.\s*be\s+consultative\.\s*be\s+clear\.",
            r"write\s+down\s+the\s+top\s+3\s+activator|find\s+a\s+partner",
            r"leveraging\s+one\s+top\s+10\s+contact|share\s+your\s+name,\s*market,\s*and\s+job\s+focus",
            r"limiting\s+beliefs\s+do\s+you\s+hold|how\s+do\s+you\s+prepare\s+to\s+have\s+an\s+effective\s+meeting",
            r"what\s+do\s+you\s+think\s+differentiates\s+partner_advisory|think\s+about\s+the\s+conversation\s+we\s+had\s+earlier",
            r"loyalty\s+is\s+changing|there\s+are\s+common\s+themes\s+driving\s+this\s+trend",
            r"the\s+buying\s+journey\s+looks\s+very\s+different|business\s+development\s+v\.\s+sales",
            r"a\s+mindset\s+is\s+a\s+collection\s+of\s+beliefs|activator\s+mindsets|activators\s+have\s+a\s+system",
            r"don[\u2019']t\s+wait\s+to\s+get\s+started|and\s+then\s+keep\s+it\s+going",
            r"who\s+we\s+are\s*:\s*from\s+our\s+website|how\s+do\s+we\s+tell\s+our\s+story\s+beyond\s+words",
            r"strong\s+and\s+successful\s+prospecting\s+begins|positioning\s+tips",
            r"tell\s+our\s+story\s+through\s+the\s+lens|differences\s+in\s+business\s+development\s+behavior",
            r"what\s+are\s+activator\s+business\s+development\s+habits|the\s+buying\s+trends\s+of\s+today",
            r"what\s+is\s+a\s+stakeholder\s+map\?|build\s+multi-threaded\s+executive\s+relationships",
            r"key\s+questions\s*[\u2026.]+\s*answered|linkedin\s+has\s+tools\s+that\s+can\s+help\s+you",
            r"go\s+to\s+the\s+linkedin\s+resource\s+page|relevant,\s*authentic\s+outreach",
            r"if\s+you\s+are\s+looking\s+for\s+bang\s+for\s+your\s+buck|effective\s+research",
            r"first,\s*we\s+prospect,\s*then\s+we\s+originate|what\s+is\s+origination",
            r"customer\s+intimacy\s+isn[\u2019']t\s+about\s+being|active\s+listening\s+tactics",
            r"avoid\s+self-serving\s+conversations|ask\s+great\s+questions|trust\s+builds\s+from\s+the\s+little\s+things",
            r"leadership\s+alignment\s+what\s+does\s+the\s+future\s+of\s+your\s+organization",
        )
    ),
    re.IGNORECASE | re.DOTALL,
)
_Q7_LOGISTICS_DIVIDER_SOCIAL_RE = re.compile(
    r"(?:\b(?:break!|be\s+back\s+at|lunch!|small\s+group\s+dinners|pre-assessment|post-assessment|scan\s+the\s+qr\s+code|today[\u2019']s\s+agenda)\b|"
    r"\b(?:matsu|wild\s+ginger|purple\s+cafe|il\s+bistro|purplewild\s+ginger)\b|"
    r"(?:0[1-9]\s+(?:welcome\s*\+|acting\s+like\s+an\s+activator|let[\u2019']s\s+play\s+a\s+game|introducing\s+partner_advisory|the\s+art\s+of\s+prospecting|building\s+customer\s+relationships|the\s+growth\s+formula|day\s+1\s+closing))|"
    r"partner_advisory\s+sales\s+immersion\s+day\s+1|maintaining\s+your\s+network\s*&\s*building\s+your\s+relationships|"
    r"rf\s*p\s+response\s+infrastructure\s+automation\s+transformation|"
    r"(?:our\s+experience|our\s+vision|workstream\s*&\s*activities|team\s+structure\s*&\s*financial\s+proposal|our\s+industry\s+expertise|qualifications\s*&\s*customer\s+stories|representative\s+team\s+profiles|commercial\s+requirements|appendix)\s+0\d|commercial\s+requirements\s+10|"
    r"workstream\s+[1-5]:\s*(?:operating\s+model\s*&\s*organizational\s+design|architecture\s*\+\s*roadmap|mobilization\s*\+\s*gcp\s+platform|aws\s+extension|program\s+strategy,\s*governance,\s*transition\s*&\s*hand-off)$|"
    r"(?:114|116|118|120)\s+(?:horizon\s+(?:one|two|three)|three\s+horizon)$|"
    r"(?:buying\s+today|buyer\s+values|seller\s+personas|activator\s+mindset|have\s+an\s+approach\s*&\s*a\s+system|leading\s+with\s+insights|don[\u2019']t\s+wait\s+to\s+activate|prioritizing\s+business\s+development|stakeholder\s+maps|be\s+prepared|origination|the\s+growth\s+formula|three\s+horizons)$)",
    re.IGNORECASE | re.DOTALL,
)
_Q8_VENDOR_BOILERPLATE_RE = re.compile(
    "|".join(
        (
            r"the\s+partner_advisory[\u2019']s\s+differentiator.*many\s+firms\s+can\s+design",
            r"relevant\s+experience.*mcdonald[\u2019']s\s+corporation",
            r"client\s+references.*relevant\s+experience",
            r"we[\u2019']re\s+transforming\s+the\s+energy\s*&\s*utilities\s+industry",
            r"our\s+partnership\s+with\s+google\s+cloud.*gemini\s+enterprise",
            r"there[\u2019']s\s+no\s+room\s+for\s+guesswork\s+when\s+it\s+comes\s+to\s+your\s+customers",
            r"generation\s+transmission\s+substation\s+distribution\s+meter\s+customer",
            r"at\s+partner_advisory\s+we[\u2019']re\s+at\s+the\s+forefront\s+of\s+helping\s+the\s+world[\u2019']s\s+leading\s+global\s+energy",
            r"in\s+2025,\s*our\s+energy\s+and\s+utilities\s+client\s+feedback|customer\s+love\s+index",
            r"production\s+outcomes\s+from\s+comparable\s+engagements|comparable\s+references",
            r"transforming\s+infrastructure\s+automation\s+at\s+a\s+multi-\s*billion-dollar\s+global\s+food",
            r"azure\s+and\s+gcp\s+devsecops\s+transformation\s+for\s+\$2b\s+life\s+science",
            r"an\s+application,\s*infrastructure\s*&\s*operations\s+team\s+at\s+a\s+large\s+financial\s+services\s+firm",
            r"a\s+pacific\s+northwest\s+investor-owned\s+utility\s+engaged\s+partner_advisory",
            r"our\s+client,\s*a\s+us\s+regional\s+bank\s+was\s+embarking",
            r"data\s*&\s*reporting\s+migration\s+acceleration\s+for\s+global\s+energy",
            r"experience\s+modification\s+rate\s*\(emr\)|workers[\u2019']\s+compensation\s+insurance",
        )
    ),
    re.IGNORECASE | re.DOTALL,
)
_CONTEXT_BIO_OR_NARRATIVE_RECAP_RE = re.compile(
    "|".join(
        (
            r"managing\s+director,\s*client\s+leadership.*dan\s+brings\s+more\s+than\s+20\s+years",
            r"senior\s+principal,\s*data\s+delivery\s+ownership.*lan\s+is\s+a\s+senior\s+transformation",
            r"senior\s+consultant,\s*delivery\s+leadership.*leticia\s+is\s+a\s+delivery\s+leader",
            r"enterprise\s+operating\s+model\s+lead.*designed\s+and\s+activated\s+an\s+operating\s+model",
            r"principal,\s*transformation.*julia\s+is\s+a\s+leader",
            r"director,\s*cloud\s+build.*jeremy\s+is\s+a\s+cloud",
            r"senior\s+director,\s*software\s+engineering.*kenny\s+is\s+a\s+technology\s+leader",
            r"principal,\s*technology\s*&\s*cloud\s+enablement.*highly\s+accomplished",
            r"migration\s+lead\s+from\s+on-premises\s+to\s+aws\s+cloud.*daniel\s+simao",
            r"senior\s+engineer,\s*platform\s+engineering.*sarah\s+is\s+an\s+engineer",
            r"senior\s+consultant,\s*amazon\s+web\s+services.*sherman\s+is\s+a\s+cloud\s+engineer",
            r"gabriel\s+is\s+a\s+resourceful\s+engineer,\s*always\s+looking\s+for\s+the\s+right",
            r"senior\s+director,\s*global\s+power\s*&\s*utilities\s+leader.*brian\s+has\s+28\+",
            r"lisa\s+blackwood.*senior\s+director,\s*client\s+leadership.*lisa\s+is\s+a\s+management",
            r"the\s+big\s+three\s+for\s+delivery\s+success.*role\s+clarity",
            r"key\s+delivery\s+enablers\s+guilding\s+design\s+principles",
            r"journey\s+start\s+journey\s+end\s+define\s+architecture",
            r"journey\s+end\s+follow\s+guided\s+request\s+process\s+with\s+real-time\s+validation",
            r"how\s+we\s+build\s+organizational\s+capability\s+our\s+delivery\s+approach",
            r"nee\s+is\s+seeking\s+to\s+establish\s+an\s+enterprise\s+infrastructure\s+automation\s+factory\s+that\s+converts",
            r"partner_advisory\s+brings\s+the\s+continuity,\s*experience,\s*and\s+delivery\s+model\s+needed\s+to\s+design\s+the\s+factory",
            r"partner_advisory\s+will\s+help\s+apex_global\s+move\s+from\s+fragmented,\s*ticket\s*-?driven\s+infrastructure\s+delivery",
            r"architecture\s*&\s*guiding\s+principles\s+six\s+principles\s+guide\s+every\s+architecture",
            r"implementation\s+approach\s+partner_advisory\s+will\s+move\s+from\s+discovery\s+to\s+working\s+automation",
            r"workstream\s+2026\s+2027",
            r"kickoff\s+december\s+2026\s+m1\s+m2\s+february\s+2027",
            r"we\s+adapt\s+scaled\s+delivery\s+incorporating\s+best\s+practices\s+to\s+support\s+reliable",
            r"three\s+connected\s+pillars\s+that\s+create\s+clarity,\s*drive\s+disciplined\s+execution",
            r"approach\s*&\s*methodology\s+one\s+integrated\s+program",
            r"our\s+operating\s+model\s+framework\s+meets\s+you\s+where\s+you\s+are",
            r"the\s+model\s+succeeds\s+when\s+nee\s+can\s+assign\s+owners,\s*prioritize\s+the\s+backlog",
            r"a\s+lightweight\s+factory\s+product\s+layer\s+coordinates\s+independently\s+owned\s+domain\s+teams",
            r"successful\s+adoption\s+must\s+be\s+intentionally\s+designed\s+into\s+project\s+delivery",
            r"our\s+human-centric\s+approach\s+to\s+change\s+management",
            r"workstream\s+key\s+activity\s+month\s+1\s+m2\s+m3\s+m4\s+m5\s+m6\s+m7\s+m8\s+m9",
            r"workstream\s+2\s+runs\s+inside\s+the\s+segment\s+a\s+window\s+and\s+completes\s+at\s+m2",
            r"partner_advisory\s+will\s+help\s+nee\s+reduce\s+manual\s+ticket\s+handling\s+by\s+improving\s+request\s+quality",
            r"month\s+1\s+begins\s+at\s+segment\s+b\s+mobilization,\s*overlapping\s+the\s+final\s+weeks",
            r"workstream\s+4\s+begins\s+as\s+the\s+gcp\s+standard\s+path\s+stabilizes\s+in\s+mid\s*-?june\s+2027",
            r"month\s+1\s+begins\s+with\s+governance\s+mobilization\s+so\s+nee\s+can\s+turn\s+segment\s+a\s+decisions|sales\s+learning\s+q4\s+2026\s+partner_advisory\s+sales\s+immersion",
            r"0[2-7]\s+(?:pursuit\s+101|leading\s+with\s+outcomes|winning\s+in\s+the\s+first|deal\s+qualification|buyers[\u2019']\s+values|mutual\s+success|working\s+together|establishing\s*&\s*expanding|negotiations\s*\+|gtm\s+approach)",
            r"would\s+anyone\s+be\s+surprised\s+to\s+find\s+out|our\s+(?:sales|pursuit)\s+process\s+is\s+a\s+sequence\s+of\s+steps|the\s+more\s+i\s+pay\s+you,\s+the\s+less\s+you\s+cost\s+me|pursuits\s+are\s+fun\.\s*pursuits\s+can\s+be\s+hard",
            r"how\s+rigorously\s+do\s+you.*follow\s+the\s+sales\s+stage|supports\s+pursuit,\s*e\.g\.,\s*examples\s+include|support\s+the\s+pursuit\s+as\s+needed\s+with\s+the\s+required|should\s+i\s+add\s+this\s+team\s+member\s+to\s+my\s+pursuit",
            r"why\s+leading\s+with\s+value\s+matters|w\s*hy\s+i\s*t\s+matters:\s*w\s*hy\s+outcomes|what\s+clients\s+ask\s+for.*eggs|we\s+drive\s+several\s+benefits\s+for\s+partner_advisory|evolved\s+approach\s+to\s+outcome\s+sales|become\s+a\s+data-\s*and\s+ai-driven\s+enterprise",
            r"getting\s+to\s+the\s+value\s+drives\s+the\s+right\s+solution|pick\s+one\s+pursuit\s+from\s+the\s+previous\s+activity|effective\s+sales\s+meetings.*consider\s+these\s+four\s+factors|the\s+meeting\s+starts\s+before\s+you\s+walk\s+in",
            r"know\s+the\s+context\s*[\-\u2013\u2014]\s*theirs\s+and\s+ours|conversation\s+prep\s+frame\s+the\s+meeting|now\s+you\s+are\s+in\s+the\s+room|what\s+are\s+you\s+trying\s+to\s+say|the\s+real\s+problem.*we\s+need\s+a\s+new\s+crm|how\s+to\s+reframe\s+the\s+conversation",
            r"structuring\s+the\s+discussion.*interested\s+in\s+acting\s+now|the\s+importance\s+of\s+peak\s+moments|keys\s+to\s+creating\s+peak\s+moments|what\s+peak\s+moments\s+have\s+you\s+experienced|treat\s+your\s+ai\s+like\s+a\s+thought\s+partner|building\s+your\s+client\s+persona",
            r"working\s+as\s+a\s+table,\s*build\s+a\s+customer\s+persona|change\s+your\s+thinking.*qualification\s+isn[\u2019']t\s+negative|qualify\s+often.*qualification\s+isn[\u2019']t\s+one\s*&\s*done|levers\s+of\s+qualification|think\s+of\s+an\s+active\s+deal\s+you\s+have\s+right\s+now",
            r"j&j[\u2019']s\s+credo\s+serves\s+as\s+the\s+foundation|customer\s+persona:\s*(?:luis\s+miranda|peter\s+gamble)|goal:\s*understand\s+the\s+customer[\u2019']s\s+business\s+problem|activity:\s*(?:prepare\s+for\s+your|meeting\s+feedback|second\s+meeting|c-suite\s+meeting)",
            r"establish\s+with\s+your\s+client\s+who\s+is\s+leading\s+the\s+meeting|better\s+2gether|black\s+bottle\s+gastrotavern|partner_advisory\s+hawk\s+tower|ai\s+ag\s*ents\s+as\s+te\s*am\s+m\s*emb\s*er\s*s|breakout\s+[1-4]|cor\s*por\s*ate\s+memo\s*ry\s+b\s*rain|b\s*reak\s*out\s+[1-4]",
            r"15\s*-minute\s+te\s*am\s+ch\s*alleng\s*e|pe\s*rsonal\s+advisor\s*y\s+note\s*book|str\s*ate\s*gic\s+pos\s*itio\s*ning\s+in\s+prac\s*tic\s*e|from\s+copilot\s+user\s+to\s+ai-\s*powered\s+seller|advanced\s+copilot\s+use\s+cases|client\s+buyer\s+roles.*who\s+is\s+the\s+buyer",
            r"customers\s+buy\s+through\s+three\s+different\s+lenses.*\b44\b|business\s+value.*organic\s+revenue|help\s+me\s+grow\s+my\s+network|honesty\s*&\s*genuineness|ask\s+intentional\s+questions\s+to\s+uncover|common\s+mistakes\s+in\s+determining\s+value|as\s+a\s+team,\s*think\s+about\s+your\s+2nd\s+mock",
            r"connecting\s+your\s+buyer\s+values\s+with\s+the\s+right\s+win\s+themes|which\s+win\s+theme\s+approach\s+do\s+you\s+prefer|how\s+to\s+create\s*win\s+themes|creating\s+powerful\s+win\s+themes|if\s+i\s+had\s+an\s+hour\s+to\s+solve\s+a\s+problem|acme\s+medical|acme\s+has\s+asked\s+partner_advisory",
            r"leverage\s+steps\s+1\s+through\s+4\s+of\s+your\s+spro|leverage\s+your\s+slide\s+during\s+your\s+meeting|image\s+layout\s+instructions|best\s+practices.*living\s+document|activity\s+status\s+notes.*mtg\s+on\s+partnership|do\s+you\s+ever\s+feel\s+like\s+you\s+are\s+going\s+it\s+alone",
            r"our\s+responsibility.*know\s+client[\u2019']s\s+strategic|selling\s+is\s+team\s+sport|think\s+about\s+a\s+pursuit\s+or\s+even\s+prospecting|how\s+many\s+c-suites\s+have\s+you\s+met\s+with|what\s+is\s+the\s+reality\s+of\s+a\s+buyer\s+in\s+the\s+c-|let[\u2019']s\s+explore\s+the\s+mindsets\s+one\s+by\s+one",
            r"regardless\s+of\s+mindset|getting\s+in\s+front\s+of\s+cxos|preparing\s+for\s+cxo\s+meetings|conducting\s+cxo\s+meetings|partner_advisory[\u2019']s\s+negotiation\s+approach|professional\s+services\s+takes\s+a\s+different\s+approach|partner\s+up\s+with\s+someone\s+at\s+your\s+table",
            r"prepare\s+for\s+negotiations.*prep\s+priorities|when\s+alignment\s+is\s+elusive|we\s+just\s+need\s+to\s+make\s+sure\s+everyone|time\s+to\s+negotiate:\s*customer\s+says|meeting\s+3:\s*exec\s*/\s*c-suite|solution:\s*an\s+offering\s+that\s+solves\s+a\s+specific|priority\s+solutions\s+have\s+internal\s+sales\s+enablement|quarterly\s+gtm\s+activation|celebrate\s+connections|closing\s+question:\s*what\s+is\s+something\s+you\s+are",
            r"contingent\s+offer\s*:\s*this\s+offer\s+is\s+contingent\s+upon|immigration\s+reform\s+and\s+control\s+act",
        )
    ),
    re.IGNORECASE | re.DOTALL,
)
_C5_TACTICAL_UI_RE = re.compile(
    r"\b(?:crm\s+ui\s+click-path|how\s+to\s+(?:log|tag\s+an\s+ams\s+opportunity)\s+in\s+salesforce|"
    r"in\s+the\s+opportunity\s+details\s+section,\s*select\s+the|room\s+booking\s+calendar|"
    r"workbook\s+instructions\s+tab)\b",
    re.IGNORECASE,
)
_C1_PORTFOLIO_OR_POD_RE = re.compile(
    r"(?:\b(?:pm:\s*[^\n]+|sm:\s*[^\n]+|geo-pod:|industry-pod:|plan\s+summary:\s*20\d\d|"
    r"business\s+unit\s+buyers|all\s+workstream\s+assignments)\b)",
    re.IGNORECASE,
)
_C2_ORG_ROSTER_RE = re.compile(
    r"(?:\b(?:reports_to|reporting\s+to|org\s+chart|"
    r"who\s+to\s+bring\s+to\s+the\s+table|engagement\s+coordinators|authorized\s+signatories)\b)",
    re.IGNORECASE,
)
_C3_COMMERCIAL_SOW_STAFFING_RE = re.compile(
    r"(?:\b(?:statement\s+of\s+work|rebate\s+credit|estimated\s+service\s+fees|fixed\s+fee|"
    r"two\s+fixed-price\s+paths|anatomy\s+of\s+.*deals|tbd[1-9]\b|invoice\s+schedule|option\s+1\s+recommended)\b)",
    re.IGNORECASE,
)
_C4_CORE_ARCHITECTURE_SOW_ANCHOR_RE = re.compile(
    r"(?:\b(?:request-to-delivery\s+flow|self-service\s+journey\s+map|domain\s+interface\s+specifications|"
    r"activity\s*\(sow\s+deliverable\)|type\s+item\s+mitigation\s*/\s*decision\s+plan|"
    r"deliverable[s]?\s+(?:a[1-4]|b[1-6])\b|milestone\s+[1-5]\b|transition\s+gate[s]?\s+g[1-3]\b|"
    r"forum\s+cadence\s+core\s+participants|lightweight\s+automation\s+factory\s+product\s+model|"
    r"digital\s+sherpa|infracodebase|it4u|backstage)\b)",
    re.IGNORECASE,
)


def strip_master_boilerplate_and_footers(text: str) -> str:
    """Strip recurring copyright footers, slide numbers, watermarks, and occluded ghost text before materiality checks."""
    if not text:
        return ""
    from dual_pipeline.pdf_source import purge_occluded_ghost_layers
    cleaned = purge_occluded_ghost_layers(text)
    raw_lines = [ln.strip() for ln in cleaned.splitlines() if ln.strip()]
    out_lines: List[str] = []
    for idx, ln in enumerate(raw_lines):
        if ln == "©":
            continue
        if re.match(r"^(?:©\s*20\d\d|20\d\d\s+PartnerAdvisory|PartnerAdvisory\.\s*All\s+Rights\s+Reserved|Proprietary\s+and\s+Confidential)", ln, re.I):
            continue
        had_footer_marker = bool(_MASTER_FOOTER_STRIP_RE.search(ln)) or "©" in ln
        ln_sub = _MASTER_FOOTER_STRIP_RE.sub("", ln).strip()
        is_trailing_line = idx == len(raw_lines) - 1 and len(raw_lines) > 1
        # Only strip standalone 1-3 digit numbers or years when part of a footer/copyright line or trailing page number
        if re.fullmatch(r"(?:©|\d{1,3}|202[4-9])", ln_sub) and (had_footer_marker or is_trailing_line):
            continue
        if ln_sub:
            out_lines.append(ln_sub)
    return "\n".join(out_lines).strip()


def is_summary_section_title(title: str) -> bool:
    """Return True when a section heading represents a front-loaded composed overview."""
    return bool(_SUMMARY_HEADING_RE.search((title or "").strip()))


def is_template_appendix_trigger(title: str, body_text: str) -> bool:
    """Return True when a slide/page marks the start of a 'READ ME THEN DELETE' / 'Account Planning Template' appendix."""
    combined = f"{title or ''}\n{body_text or ''}"
    return bool(
        re.search(
            r"(?:read\s+me\s+then\s+delete|account\s+planning\s+template\b)",
            combined,
            re.IGNORECASE,
        )
    )


_QUARANTINE_RULES: Tuple[Tuple[re.Pattern[str], str, str], ...] = (
    (_Q4_ILLUSTRATIVE_MOCKUP_RE, UnitDispositionClass.Q4_QUARANTINE_ILLUSTRATIVE_MOCKUP.value, "QUARANTINED"),
    (_Q1_TEMPLATE_SCAFFOLD_RE, UnitDispositionClass.Q1_QUARANTINE_TEMPLATE_SCAFFOLD.value, "QUARANTINED"),
    (_Q2_UNMODIFIED_DEFAULT_RE, UnitDispositionClass.Q2_QUARANTINE_UNMODIFIED_TEMPLATE_DEFAULT.value, "QUARANTINED"),
    (_Q3_WORKBOOK_EMPTY_OR_LOOKUP_RE, UnitDispositionClass.Q3_QUARANTINE_WORKBOOK_EMPTY_AND_LOOKUP_GRIDS.value, "QUARANTINED"),
    (_Q5_LEGAL_BOILERPLATE_RE, UnitDispositionClass.Q5_QUARANTINE_LEGAL_BOILERPLATE.value, "QUARANTINED"),
    (_Q6_PEDAGOGICAL_PARABLES_RE, UnitDispositionClass.Q6_QUARANTINE_PEDAGOGICAL_AND_PARABLES.value, "QUARANTINED"),
    (_Q7_LOGISTICS_DIVIDER_SOCIAL_RE, UnitDispositionClass.Q7_QUARANTINE_LOGISTICS_DIVIDER_SOCIAL.value, "STRUCTURAL_DIVIDER"),
    (_Q8_VENDOR_BOILERPLATE_RE, UnitDispositionClass.Q8_QUARANTINE_VENDOR_BOILERPLATE.value, "QUARANTINED"),
)

_PROMOTED_RULES: Tuple[Tuple[re.Pattern[str], str, str, bool], ...] = (
    (_C5_TACTICAL_UI_RE, UnitDispositionClass.C5_TACTICAL_UI_OR_PROCESS_CADENCE.value, "TACTICAL_CONTEXT", False),
    (_C1_PORTFOLIO_OR_POD_RE, UnitDispositionClass.C1_CORE_PORTFOLIO_OR_POD_MATRIX.value, "SUBSTANTIVE", True),
    (_C2_ORG_ROSTER_RE, UnitDispositionClass.C2_CORE_ORG_ROSTER_AND_VISUAL_TREE.value, "SUBSTANTIVE", True),
    (_C3_COMMERCIAL_SOW_STAFFING_RE, UnitDispositionClass.C3_CORE_COMMERCIAL_DEAL_SOW_AND_STAFFING.value, "SUBSTANTIVE", True),
)


def _match_quarantine_rule(combined: str) -> Optional[Tuple[str, str, bool]]:
    for pattern, s_cls, mat in _QUARANTINE_RULES:
        if pattern.search(combined):
            return s_cls, mat, False
    return None


def _match_promoted_rule(combined: str) -> Tuple[str, str, bool]:
    for pattern, s_cls, mat, is_sub in _PROMOTED_RULES:
        if pattern.search(combined):
            return s_cls, mat, is_sub
    if _CONTEXT_BIO_OR_NARRATIVE_RECAP_RE.search(combined):
        return UnitDispositionClass.CONTEXT_NARRATIVE_OR_RECAP.value, "SUMMARY_OVERVIEW", False
    if _C4_CORE_ARCHITECTURE_SOW_ANCHOR_RE.search(combined):
        return UnitDispositionClass.C4_CORE_ARCHITECTURE_ROADMAP_POLICY.value, "SUBSTANTIVE_ANCHOR", True
    return UnitDispositionClass.C4_CORE_ARCHITECTURE_ROADMAP_POLICY.value, "SUBSTANTIVE", True


def classify_unit_sieve(title: str, body_text: str, image_count: int = 0) -> Tuple[str, str, bool]:
    """
    Classify a document unit into the 12-Class Unit Materiality & Quarantine Sieve.
    Returns (sieve_class, legacy_materiality, is_substantive).
    """
    clean_title = strip_master_boilerplate_and_footers(title or "")
    cleaned_body = strip_master_boilerplate_and_footers(body_text or "")
    body_lines = [ln.strip() for ln in cleaned_body.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    non_header_text = " ".join(body_lines).strip()
    combined = f"{clean_title}\n{non_header_text}".strip()

    q_hit = _match_quarantine_rule(combined)
    if q_hit is not None:
        return q_hit
    if is_summary_section_title(clean_title):
        return "SUMMARY_OVERVIEW", "SUMMARY_OVERVIEW", False

    has_numeric_or_bullet = bool(
        re.search(r"(?:\$\d|\b\d+%|\b20\d\d\b|^\s*[-*•]|\|)", non_header_text, re.MULTILINE)
    )
    if (
        (_NON_SUBSTANTIVE_TITLE_RE.match(clean_title) and len(non_header_text) < 80)
        or (len(non_header_text) < 55 and not has_numeric_or_bullet and image_count == 0)
        or (len(non_header_text) < 40 and not has_numeric_or_bullet and len(body_lines) <= 3)
    ):
        return UnitDispositionClass.Q7_QUARANTINE_LOGISTICS_DIVIDER_SOCIAL.value, "STRUCTURAL_DIVIDER", False

    return _match_promoted_rule(combined)


def classify_unit_materiality(title: str, body_text: str, image_count: int = 0) -> str:
    """Classify a slide or page unit as SUBSTANTIVE, SUMMARY_OVERVIEW, QUARANTINED, or STRUCTURAL_DIVIDER."""
    _, materiality, _ = classify_unit_sieve(title, body_text, image_count)
    return materiality


def _build_slide_section(idx: int, line: str, m_slide: re.Match[str]) -> Dict[str, Any]:
    slide_num = int(m_slide.group(2))
    slide_title = (m_slide.group(3) or f"Slide {slide_num}").strip()
    return {
        "kind": "slide", "slide_number": slide_num, "title": slide_title,
        "locator": f"slide:{slide_num}", "start_line": idx, "lines": [line], "is_summary_unit": False,
    }


def _build_summary_section(idx: int, line: str, title: str) -> Dict[str, Any]:
    return {
        "kind": "summary_section", "slide_number": 0, "title": title.strip(),
        "locator": "section:executive_overview", "start_line": idx, "lines": [line], "is_summary_unit": True,
    }


def _is_explicit_template_worksheet_unit(title: str, body_text: str) -> bool:
    """Return True only when the unit itself is an explicit template scaffold or unfilled worksheet."""
    combined = f"{title or ''}\n{body_text or ''}"
    if is_template_appendix_trigger(title, body_text) or _Q1_TEMPLATE_SCAFFOLD_RE.search(combined):
        return True
    return bool(re.search(
        r"(?:\b2027\s+account\s+planning\b.*\b(?:instructions|template|example|guidance|fill\s+in|replace\s+text)\b|"
        r"\b(?:instructions|guidance)\s+for\s+completing\b|\bplaceholder\s+text\b|\bclick\s+to\s+add\s+text\b)",
        combined, re.IGNORECASE | re.DOTALL,
    ))


def _section_to_source_unit(sec: Dict[str, Any], source_hash: str, format_name: str, in_template_appendix: bool = False) -> SourceUnit:
    raw_content = "\n".join(sec["lines"]).strip()
    from dual_pipeline.pdf_source import purge_occluded_ghost_layers
    content = purge_occluded_ghost_layers(raw_content, document_title=sec.get("title"))
    loc = sec["locator"]
    is_sum = bool(sec.get("is_summary_unit"))
    if is_sum:
        sieve_class, materiality, is_sub = "SUMMARY_OVERVIEW", "SUMMARY_OVERVIEW", False
    else:
        sieve_class, materiality, is_sub = classify_unit_sieve(sec["title"], content)
        if in_template_appendix and _is_explicit_template_worksheet_unit(sec["title"], content):
            sieve_class = UnitDispositionClass.Q1_QUARANTINE_TEMPLATE_SCAFFOLD.value
            materiality, is_sub = "QUARANTINED", False
    return SourceUnit(
        unit_id=f"{source_hash[:12]}:{loc}", source_hash=source_hash, format=format_name,
        unit_type="summary_section" if is_sum else "slide", locator=loc, content=content,
        metadata={
            "slide_number": sec["slide_number"], "page_num": sec["slide_number"] if sec["slide_number"] > 0 else None,
            "title": sec["title"], "start_line": sec["start_line"], "end_line": sec["end_line"],
            "is_summary_unit": is_sum, "is_substantive": is_sub, "materiality": materiality,
            "sieve_class": sieve_class, "disposition": sieve_class,
        },
    )


def _finalize_slide_units(sections: List[Dict[str, Any]], source_hash: str, format_name: str) -> List[SourceUnit]:
    out_units: List[SourceUnit] = []
    in_template_appendix = False
    for sec in sections:
        if is_template_appendix_trigger(sec.get("title", ""), "\n".join(sec["lines"])):
            in_template_appendix = True
        out_units.append(_section_to_source_unit(sec, source_hash, format_name, in_template_appendix=in_template_appendix))
    return out_units


def extract_slide_and_section_units(raw_text: str, source_hash: str, format_name: str = "md") -> List[SourceUnit]:
    """Decompose Markdown or text decks containing Slide/Page headers into structural slide units."""
    lines = raw_text.splitlines()
    if not lines:
        return []
    sections: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    for idx, line in enumerate(lines, start=1):
        stripped = line.strip()
        m_slide = _SLIDE_OR_PAGE_HEADING_RE.match(stripped)
        if m_slide:
            if current is not None:
                current["end_line"] = idx - 1
                sections.append(current)
            current = _build_slide_section(idx, line, m_slide)
            continue
        m_h2 = re.match(r"^(#{1,3})\s+(.+)$", stripped)
        if m_h2 and current is None and is_summary_section_title(m_h2.group(2)):
            current = _build_summary_section(idx, line, m_h2.group(2))
            continue
        if current is not None:
            current["lines"].append(line)
    if current is not None:
        current["end_line"] = len(lines)
        sections.append(current)
    if sum(1 for s in sections if s["kind"] == "slide") < 2:
        return []
    return _finalize_slide_units(sections, source_hash, format_name)


def resolve_locator_for_offset(document_text: str, start_offset: int) -> str:
    """Resolve the enclosing Slide N, Page N, or Line L<n> locator for a character offset."""
    if not document_text or start_offset < 0:
        return "L1"
    capped_offset = min(len(document_text), start_offset)
    line_end = document_text.find("\n", capped_offset)
    prefix = document_text[: line_end if line_end != -1 else len(document_text)]
    lines = prefix.splitlines()
    line_no = max(1, len(lines))
    for ln in reversed(lines):
        m = _SLIDE_OR_PAGE_HEADING_RE.match(ln.strip())
        if m:
            kind = "Page" if "page" in ln.lower() else "Slide"
            return f"{kind} {int(m.group(2))}"
    return f"L{line_no}"


def partition_units_into_batches(
    units: List[Dict[str, Any]],
    batch_size: int = 8,
) -> List[List[Dict[str, Any]]]:
    """Partition non-summary structural slide/page units into bounded extraction batches."""
    substantive = [
        u for u in units
        if isinstance(u, dict)
        and not (u.get("metadata") or {}).get("is_summary_unit")
        and (u.get("metadata") or {}).get("is_substantive", True) is not False
        and (u.get("metadata") or {}).get("sieve_class") not in QUARANTINED_UNIT_CLASSES
        and (u.get("metadata") or {}).get("disposition") not in QUARANTINED_UNIT_CLASSES
    ]
    if not substantive:
        return []
    step = max(1, batch_size)
    return [substantive[i : i + step] for i in range(0, len(substantive), step)]


def _remap_eids(raw_eids: Any, id_remap: Dict[str, str], merged_ev: Dict[str, Any]) -> List[str]:
    if id_remap:
        return [id_remap[x] for x in (raw_eids or []) if x in id_remap]
    return [x for x in (raw_eids or []) if x in merged_ev]


def _merge_batch_evidence(
    ev_idx: Dict[str, Any],
    merged_ev: Dict[str, Dict[str, Any]],
    text_loc_to_ev_id: Dict[Tuple[str, str], str],
) -> Dict[str, str]:
    id_remap: Dict[str, str] = {}
    for old_eid, ev_obj in ev_idx.items():
        if not isinstance(ev_obj, dict):
            continue
        exact = str(ev_obj.get("exact_text") or ev_obj.get("text") or "").strip()
        loc_str = json.dumps(ev_obj.get("locator") or ev_obj.get("source_locator") or "", sort_keys=True)
        dedup_key = (exact, loc_str)
        if dedup_key in text_loc_to_ev_id:
            id_remap[old_eid] = text_loc_to_ev_id[dedup_key]
        else:
            new_eid = f"ev-{len(merged_ev) + 1:02d}"
            ev_copy = dict(ev_obj)
            ev_copy["evidence_id"] = new_eid
            merged_ev[new_eid] = ev_copy
            text_loc_to_ev_id[dedup_key] = new_eid
            id_remap[old_eid] = new_eid
    return id_remap


def _merge_batch_entities(
    entities: List[Any],
    id_remap: Dict[str, str],
    merged_ev: Dict[str, Any],
    merged_entities: Dict[str, Dict[str, Any]],
) -> None:
    for ent in entities:
        if not isinstance(ent, dict) or not ent.get("id"):
            continue
        eid = str(ent["id"]).strip()
        remapped_eids = _remap_eids(ent.get("evidence_ids"), id_remap, merged_ev)
        if eid not in merged_entities:
            merged_entities[eid] = {**ent, "evidence_ids": list(dict.fromkeys(remapped_eids))}
        else:
            existing_eids = merged_entities[eid].get("evidence_ids") or []
            merged_entities[eid]["evidence_ids"] = list(dict.fromkeys(existing_eids + remapped_eids))


def _merge_batch_signals(
    signals: List[Any],
    id_remap: Dict[str, str],
    merged_ev: Dict[str, Any],
    merged_signals: Dict[Tuple[str, str, str], Dict[str, Any]],
) -> None:
    for sig in signals:
        if not isinstance(sig, dict):
            continue
        triple = (
            str(sig.get("from_id") or ""),
            str(sig.get("predicate") or sig.get("relationship") or ""),
            str(sig.get("to_id") or ""),
        )
        if not all(triple):
            continue
        remapped_eids = _remap_eids(sig.get("evidence_ids"), id_remap, merged_ev)
        if triple not in merged_signals:
            merged_signals[triple] = {
                **sig,
                "signal_id": f"sig-{len(merged_signals) + 1:02d}",
                "evidence_ids": list(dict.fromkeys(remapped_eids)),
            }
        else:
            existing_eids = merged_signals[triple].get("evidence_ids") or []
            merged_signals[triple]["evidence_ids"] = list(dict.fromkeys(existing_eids + remapped_eids))


def _merge_batch_keyed_updates(
    items: List[Any],
    id_field: str,
    prefix: str,
    id_remap: Dict[str, str],
    merged_ev: Dict[str, Any],
    target: Dict[str, Dict[str, Any]],
) -> None:
    for item in items:
        if isinstance(item, dict):
            uid = str(item.get(id_field) or item.get("id") or f"{prefix}-{len(target) + 1:02d}")
            remapped_eids = _remap_eids(item.get("evidence_ids"), id_remap, merged_ev)
            if uid not in target:
                target[uid] = {**item, "evidence_ids": list(dict.fromkeys(remapped_eids))}
            else:
                existing_eids = target[uid].get("evidence_ids") or []
                target[uid]["evidence_ids"] = list(dict.fromkeys(existing_eids + remapped_eids))


def merge_unit_batch_projections(batch_payloads: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Deterministically merge multiple unit-batch projections with sequential evidence renumbering."""
    if not batch_payloads:
        return {"evidence_index": {}, "graph_mutations": {}}

    base = json.loads(json.dumps(batch_payloads[0]))
    merged_ev: Dict[str, Dict[str, Any]] = {}
    text_loc_to_ev_id: Dict[Tuple[str, str], str] = {}
    merged_entities: Dict[str, Dict[str, Any]] = {}
    merged_signals: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    merged_decisions: Dict[str, Dict[str, Any]] = {}
    merged_proposals: Dict[str, Dict[str, Any]] = {}
    merged_risks: Dict[str, Dict[str, Any]] = {}
    merged_intents: Dict[str, Dict[str, Any]] = {}

    for payload in batch_payloads:
        id_remap = _merge_batch_evidence(payload.get("evidence_index") or {}, merged_ev, text_loc_to_ev_id)
        muts = payload.get("graph_mutations") or {}
        _merge_batch_entities(muts.get("entity_upserts") or [], id_remap, merged_ev, merged_entities)
        _merge_batch_signals(muts.get("relationship_signals") or [], id_remap, merged_ev, merged_signals)
        _merge_batch_keyed_updates(muts.get("decision_updates") or [], "decision_id", "dec", id_remap, merged_ev, merged_decisions)
        _merge_batch_keyed_updates(muts.get("proposal_updates") or [], "proposal_id", "prop", id_remap, merged_ev, merged_proposals)
        _merge_batch_keyed_updates(muts.get("risk_updates") or [], "risk_id", "risk", id_remap, merged_ev, merged_risks)
        _merge_batch_keyed_updates(muts.get("strategic_intent_signals") or [], "signal_id", "intent", id_remap, merged_ev, merged_intents)

    base["evidence_index"] = merged_ev
    base_muts = base.setdefault("graph_mutations", {})
    base_muts["entity_upserts"] = list(merged_entities.values())
    base_muts["relationship_signals"] = list(merged_signals.values())
    base_muts["decision_updates"] = list(merged_decisions.values())
    base_muts["proposal_updates"] = list(merged_proposals.values())
    base_muts["risk_updates"] = list(merged_risks.values())
    base_muts["strategic_intent_signals"] = list(merged_intents.values())
    from dual_pipeline.document_pipeline_core.orals_proposal_extractor import consolidate_section_projections
    return consolidate_section_projections(base)
