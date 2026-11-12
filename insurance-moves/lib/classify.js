// Turns a headline/summary into a structured "management move".
// Shared by the collector (Node) and the app (browser), so it has no dependencies.

export const TYPES = ['Appointment', 'Promotion', 'Succession', 'Departure', 'Retirement'];
export const LEVELS = ['C-suite', 'President / MD', 'EVP / SVP', 'VP', 'Head / Director', 'Board', 'Manager'];
export const SECTORS = ['P&C', 'Life & Annuity', 'Health', 'Reinsurance', 'Broker / Agency', 'Specialty / E&S / Lloyd\'s', 'InsurTech', 'Title / Surety'];
export const FUNCTIONS = ['General management', 'Underwriting', 'Claims', 'Actuarial', 'Distribution / Sales', 'Finance / Investments',
  'Technology / Data', 'Operations', 'Risk', 'Legal / Compliance', 'People / HR', 'Marketing'];

// ---- Titles (management level) ------------------------------------------------------------
const ACRONYMS = 'CEO|CFO|COO|CUO|CRO|CIO|CTO|CMO|CDO|CCO|CHRO|CAO|CSO|CPO|CISO|CDAO|CGO|CNO';
const TITLE_RULES = [
  // [level, regex]  — order matters only for overlap handling; highest level wins anyway.
  ['Board', /\b(?:chairman|chairwoman|chairperson|chair of the board|board chair|non[- ]executive director|independent director|board of directors|board member|joins? (?:the |its )?board|to (?:the|its) board|lead director)\b/gi],
  ['C-suite', /\b(?:(?:group|global|deputy|interim|acting|regional|divisional|executive) )?chief [a-z&,' -]{0,40}?officer\b/gi],
  ['C-suite', new RegExp(`\\b(?:${ACRONYMS})\\b`, 'g')],
  ['C-suite', /\bchief executive\b/gi],
  ['EVP / SVP', /\b(?:senior|executive) vice[- ]president\b|\b(?:EVP|SVP)\b/gi],
  ['VP', /\b(?:assistant |associate )?vice[- ]president\b|\b(?:VP|AVP)\b/g],
  ['President / MD', /\bpresident\b|\bmanaging director\b|\bgeneral manager\b|\bcountry manager\b|\bmanaging partner\b/gi],
  ['Head / Director', /\b(?:global |regional |national |group |divisional )?head of (?:the )?[A-Za-z&/'-]+(?: (?!at\b|for\b|to\b|in\b|with\b|and\b|from\b|as\b|after\b|on\b)[A-Za-z&/'-]+){0,3}/gi],
  ['Head / Director', /\b(?:[a-z]+ )?director\b|\bpractice leader\b|\bcountry head\b/gi],
  ['Manager', /\b(?:regional |branch |national |senior |general )?(?:[a-z]+ )?manager\b/gi],
];
const SUFFIXED = new Set(['EVP / SVP', 'VP', 'President / MD', 'Head / Director']);
const STOP = '(?!at\\b|for\\b|to\\b|in\\b|with\\b|and\\b|from\\b|as\\b|after\\b|on\\b|effective\\b|the\\b|who\\b|replacing\\b|succeeding\\b)';
const SUFFIX = new RegExp(`^(?:,| of| for) ${STOP}[A-Za-z&/'-]+(?: ${STOP}[A-Za-z&/'-]+){0,2}`);
const LEVEL_RANK = Object.fromEntries(LEVELS.map((l, i) => [l, i]));

export function findTitles(text) {
  const found = [];
  for (const [level, re] of TITLE_RULES) {
    re.lastIndex = 0;
    let m;
    while ((m = re.exec(text))) {
      const s = m[0];
      const before = text.slice(Math.max(0, m.index - 6), m.index).toLowerCase();
      // "vice president" must not count as president; "board of directors" is not a director role.
      if (level === 'President / MD' && /^president$/i.test(s) && /vice[ -]$/.test(before)) continue;
      if (level === 'Head / Director' && /director$/i.test(s) &&
          /(non[- ]executive|independent|board of) director/i.test(text.slice(Math.max(0, m.index - 16), m.index + s.length + 1))) continue;
      if (level === 'Head / Director' && /^(?:film|art|creative|funeral|casting|music) director$/i.test(s)) continue;
      let full = s.trim();
      if (SUFFIXED.has(level) && !/^[A-Z]{2,4}$/.test(full) || /^(?:EVP|SVP|VP|AVP)$/.test(full)) {
        const tail = text.slice(m.index + s.length).match(SUFFIX);
        if (tail) full += tail[0];
      }
      found.push({ level, text: full, index: m.index });
    }
  }
  return found;
}

function tidyRole(s) {
  if (/^[A-Z]{2,5}$/.test(s)) return s;
  return s.replace(/\s+/g, ' ').replace(/\b([a-z])([a-z&']*)/g, (w, a, b) =>
    /^(of|and|the|for|&)$/.test(w) ? w : a.toUpperCase() + b);
}

export function bestTitle(text) {
  const titles = findTitles(text);
  if (!titles.length) return null;
  // Highest seniority first (Board ranks below VP for "level" but a board move is still a board move);
  titles.sort((a, b) => LEVEL_RANK[a.level] - LEVEL_RANK[b.level] || a.index - b.index);
  const nonBoard = titles.find(t => t.level !== 'Board' && t.level !== 'Manager');
  const top = nonBoard || titles[0];
  return { level: top.level, role: tidyRole(top.text) };
}

// ---- Move type ------------------------------------------------------------------------------
const RE = {
  retire: /\bretir(?:e|es|ed|ing|ement)\b/i,
  depart: /\b(?:steps? down|stepping down|step aside|to leave|leaves|leaving|left the|depart(?:s|ed|ing|ure)?|exits?|exited|resign(?:s|ed|ing|ation)?|ousted|fired|terminated|out as|parts ways|let go|exodus)\b/i,
  promote: /\b(?:promot(?:e|es|ed|ion)|elevat(?:e|es|ed|ion)|expands? (?:his|her|their) role|expanded role|rises to|steps up to|upped to|moves up)\b/i,
  appoint: /\b(?:appoint(?:s|ed|ment)?|names?|named|hires?|hired|hiring|joins?|joined|to join|taps?|tapped|selects?|selected|recruits?|recruited|welcomes?|welcomed|adds?|added|poach(?:es|ed)?|lands?|elect(?:s|ed)?|succeed(?:s|ed)?|successor|nominat(?:es|ed)|brings on|takes (?:the )?helm|to lead|to head|new (?:ceo|cfo|coo|president|chief|head|chair)|announces? .{0,40}\b(?:as|new)\b)/i,
};

export function moveType(text) {
  const r = RE.retire.test(text), d = RE.depart.test(text), p = RE.promote.test(text), a = RE.appoint.test(text);
  if ((r || d) && (a || p) && /\b(?:succe(?:ed|ssor|ssion)|replace[sd]?|to be (?:named|appointed)|interim)\b/i.test(text)) return 'Succession';
  if ((r || d) && (a || p) && /\b(?:named|appointed|to succeed|will succeed)\b/i.test(text)) return 'Succession';
  if (r) return 'Retirement';
  if (d && !a && !p) return 'Departure';
  if (p) return 'Promotion';
  if (a) return 'Appointment';
  if (d) return 'Departure';
  return null;
}

// ---- Insurance relevance, sector, function -------------------------------------------------
const INSURANCE = /\b(?:insur|reinsur|underwrit|broker|brokerage|mga|managing general|lloyd['’]?s|carrier|actuar|annuit|claims|p&c|casualty|mutual|assurance|indemnity|syndicate|surety|e&s|excess and surplus|specialty lines|wholesale|policyholder|premium|risk solutions|bcbs|blue cross|health plan|medicare advantage|title insurance|managing agent)/i;
export const isInsuranceRelated = text => INSURANCE.test(text);

const SECTOR_RULES = [
  ['Reinsurance', /\b(?:reinsur\w*|retrocession|insurance[- ]linked|ILS|cat(?:astrophe)? bonds?|Swiss Re|Munich Re|Hannover Re|SCOR|RenaissanceRe|Gen Re|PartnerRe|Everest Re|TransRe|Odyssey Re|Validus|Aspen Re)\b/i],
  ['Broker / Agency', /\b(?:[Bb]roker(?:age|s|ing)?|[Ii]ntermediar\w*|[Aa]gency|[Aa]gencies|Marsh|McLennan|Aon|WTW|Willis|Gallagher|Brown & Brown|Lockton|Hub International|USI|Acrisure|NFP|Alliant|AssuredPartners|Howden|Ryan Specialty|Amwins|CRC Group|BRP|Baldwin|Risk Strategies|Hilb|EPIC|IMA|Truist Insurance|McGriff)\b/],
  ['Specialty / E&S / Lloyd\'s', /\b(?:MGAs?|MGUs?|managing general|program administrator|specialty|E&S|excess and surplus|Lloyd['’]?s|syndicate|managing agent|Beazley|Hiscox|Markel|Kinsale|RLI|Skyward|Bowhead|Canopius|Brit|Apollo|Ascot|Convex|Fidelis|Lancashire|Conduit)\b/i],
  ['Life & Annuity', /\b(?:life insur\w*|life (?:&|and) annuity|annuit\w*|life company|life insurer|MetLife|Prudential|Lincoln Financial|Principal Financial|Pacific Life|Athene|Brighthouse|Corebridge|Globe Life|Primerica|Jackson Financial|Equitable|Mass ?Mutual|New York Life|Northwestern Mutual|Guardian Life|Protective Life|Transamerica|Securian|Penn Mutual|Allianz Life|F&G|American Equity|Legal & General|Aviva|Phoenix Group|Manulife|Sun Life|Great-West|Empower)\b/i],
  ['Health', /\b(?:health insur\w*|health plans?|healthcare|medicare|medicaid|managed care|UnitedHealth\w*|Elevance|Anthem|Cigna|Humana|Centene|Molina|Aetna|CVS Health|Blue Cross|Blue Shield|BCBS|Kaiser|Oscar Health|Clover Health|Highmark|HCSC|Florida Blue|payer|dental|vision benefits)\b/i],
  ['InsurTech', /\b(?:insurtech\w*|Lemonade|Root Insurance|Hippo|Clearcover|Kin Insurance|Next Insurance|Pie Insurance|Coalition|At-Bay|Openly|Branch Insurance|Corvus|Vouch|Embroker|Newfront|Policygenius|Ethos|Ladder|Bestow|Duck Creek|Guidewire|Majesco|Sapiens|EIS|Insurity|CCC Intelligent|embedded insurance)\b/i],
  ['Title / Surety', /\b(?:title insur\w*|surety|fidelity bonds?|First American|Fidelity National|Stewart Title|Old Republic Title|Doma)\b/i],
  ['P&C', /\b(?:property(?: and |&| & )casualty|P&C|casualty|auto insur\w*|homeowners?|commercial lines|personal lines|workers['’]? comp\w*|Travelers|Chubb|Allstate|Progressive|Liberty Mutual|Nationwide|State Farm|Hartford|AIG|Zurich|Allianz|AXA|CNA|W\. ?R\. Berkley|Berkley|Cincinnati Financial|Erie Insurance|Auto-Owners|American Family|USAA|Farmers|Selective|Hanover|Kemper|Mercury|Arch|Everest|QBE|Tokio Marine|Sompo|MS&AD|Fairfax|Sentry|Amerisure|Westfield|Grange|Donegal|Hiscox|Intact|Definity|Aviva|RSA|Generali|Mapfre|Swiss Re Corporate|Munich Re Specialty|Great American|Philadelphia Insurance|Utica|EMC|Acuity|Frankenmuth|Main Street America|Plymouth Rock|NJM|Amica|GEICO|Berkshire Hathaway Specialty|AXIS|Argo|Hamilton|Sirius|Tiptree|Universal Insurance|Heritage|HCI|Openly)\b/i],
];
export function sectors(text) {
  const out = SECTOR_RULES.filter(([name, re]) => re.test(text) || (name === 'Reinsurance' && /\b[A-Z][A-Za-z]+ Re\b/.test(text)))
    .map(([s]) => s);
  // "Casualty treaty" at a reinsurer is reinsurance, not primary P&C.
  return out.includes('Reinsurance') ? out.filter(s => s !== 'P&C') : out;
}

const FUNC_RULES = [
  ['Underwriting', /\bunderwrit\w*|\bCUO\b|\bportfolio management\b/i],
  ['Claims', /\bclaims?\b/i],
  ['Actuarial', /\bactuar\w*|\bpricing\b|\breserving\b/i],
  ['Finance / Investments', /\bfinanc\w*|\bCFO\b|\btreasur\w*|\binvestment\w*|\baccounting\b|\bcontroller\b|\bcapital\b/i],
  ['Technology / Data', /\btechnolog\w*|\bdigital\b|\bdata\b|\banalytics\b|\b(?:CTO|CIO|CDO|CDAO|CISO)\b|\binformation\b|\bAI\b|\binnovation\b|\btransformation\b/i],
  ['Risk', /\brisk officer\b|\bCRO\b|\benterprise risk\b|\brisk management\b/i],
  ['Legal / Compliance', /\bgeneral counsel\b|\blegal\b|\bcompliance\b|\bregulatory\b|\bCLO\b/i],
  ['People / HR', /\bhuman resources\b|\bpeople\b|\btalent\b|\bCHRO\b|\bHR\b/i],
  ['Marketing', /\bmarketing\b|\bbrand\b|\bCMO\b|\bcommunications\b|\bcustomer experience\b/i],
  ['Distribution / Sales', /\bdistribution\b|\bsales\b|\bbroker relations\b|\bbusiness development\b|\bgrowth\b|\bproducer\b|\bagency\b|\bclient\b|\bCGO\b|\bpartnerships?\b/i],
  ['Operations', /\boperations?\b|\boperating\b|\bCOO\b|\bservice\b/i],
  ['General management', /\bCEO\b|\bchief executive\b|(?<!vice[- ])\bpresident\b|\bmanaging director\b|\bgeneral manager\b|\bcountry manager\b|\bchair\w*/i],
];
export function jobFunction(role, text = '') {
  for (const src of [role || '', text]) {
    const hit = FUNC_RULES.find(([, re]) => re.test(src));
    if (hit) return hit[0];
  }
  return null;
}

// ---- Person / company (heuristics on headlines) --------------------------------------------
const NAME = "[A-Z][a-zA-Z'’.-]+(?: (?:[A-Z]\\.|[A-Z][a-zA-Z'’.-]+|de|van|von|da|del|di|le|la|Mc[A-Z][a-z]+)){1,3}(?![a-zA-Z])";
const NOT_NAME = /^(?:new|chief|senior|executive|head|global|group|interim|acting|former|veteran|insurance|insurer|reinsurer|broker|board|the|its|president|vice|deputy|regional|national|managing|general|director|ceo|cfo|coo|svp|evp|vp|industry|top|longtime|long-time|two|three|four|several|leadership|management|company|firm|us|uk|u\.s\.)\b/i;
const COMPANY_PREFIX = /^(?:former |insurer |reinsurer |broker |carrier |insurance (?:giant|broker|carrier) |specialty insurer |mutual |the )+/i;
const VERB_ACTIVE = '(?:appoints|names|hires|taps|promotes|elevates|welcomes|adds|selects|recruits|announces|elects|poaches|lands|brings on|nominates)';
const VERB_PASSIVE = '(?:joins|named|appointed|to join|to lead|to head|promoted|elevated|steps down|to step down|stepping down|to retire|retires|retiring|departs|leaves|to leave|resigns|exits|takes|tapped|hired|elected|moves|returns|set to)';

function cleanName(n) {
  if (!n) return null;
  n = n.replace(/['’]s$/, '').replace(/[,.;:]+$/, '').trim();
  if (NOT_NAME.test(n) || n.split(' ').length < 2) return null;
  if (findTitles(n).length) return null;
  return n;
}
function cleanCompany(c) {
  if (!c) return null;
  c = c.replace(COMPANY_PREFIX, '').replace(/^(?:the )?(?:board of )?directors of /i, '').replace(/['’]s$/, '').replace(/[,;:]+$/, '').trim();
  if (!c || c.length > 60 || /^(?:as|to|new|its|the|a|an)$/i.test(c)) return null;
  if (findTitles(c).length) return null;
  return c;
}

export function people(text) {
  let person = null, company = null, m;
  const t = text.replace(/\s+/g, ' ');
  // "Acme Mutual appoints Jane Doe as CFO"
  if ((m = t.match(new RegExp(`^(.{2,60}?) ${VERB_ACTIVE} (?:(?:new |former |veteran |longtime )?(?:[A-Za-z&-]+ ){0,4}(?:executive|exec|leader|veteran) )?(${NAME})`)))) {
    company = cleanCompany(m[1]); person = cleanName(m[2]);
    if (!company) person = person || null;
  }
  if (!company && (m = t.match(new RegExp(`^(.{2,60}?) ${VERB_ACTIVE}\\b`)))) company = cleanCompany(m[1]);
  // "Jane Doe joins Acme as CFO" / "Jane Doe named CEO of Acme"
  if (!person && (m = t.match(new RegExp(`^(?:(?:[Ff]ormer|[Vv]eteran|[Ll]ongtime) [A-Za-z&' -]{0,40}? )?(${NAME}),? ${VERB_PASSIVE}\\b`)))) {
    person = cleanName(m[1]);
  }
  // "Acme's CFO Jane Doe to retire" / "Acme CEO Jane Doe steps down"
  if (!person && (m = t.match(new RegExp(`^(.{2,50}?)(?:['’]s)? (?:[Nn]ew |[Ff]ormer |[Oo]utgoing |[Ii]nterim |[Gg]roup |[Gg]lobal )?(?:${ACRONYMS}|[Cc]hief [A-Za-z ]+?[Oo]fficer|[Pp]resident|[Cc]hair(?:man)?|[Hh]ead of [A-Za-z ]+?),? (${NAME})`)))) {
    company = company || cleanCompany(m[1]); person = cleanName(m[2]);
  }
  // SEC style: "...appointed Ms. Jane Doe..." / "Mr. John Smith notified..."
  if (!person && (m = t.match(new RegExp(`(?:Mr\\.|Ms\\.|Mrs\\.|Dr\\.) ?(${NAME})`)))) person = cleanName(m[1]);
  if (!person && (m = t.match(new RegExp(`(?:appointed|elected|named|promoted|hired) (${NAME})`)))) person = cleanName(m[1]);
  if (!person && (m = t.match(new RegExp(`(${NAME}),? (?:the Company['’]s |our )?[A-Za-z ,]{0,60}?(?:notified|informed|announced (?:his|her|their) (?:intention|decision)|tendered|resigned|will retire)`)))) person = cleanName(m[1]);
  // Company after the person: "joins Acme as", "leaves Acme", "CEO of Acme"
  if (!company && (m = t.match(/\b(?:joins|join|leaves|leave|departs|exits|to lead|rejoins|returns to|moves to|heads to|of|at) ((?:[A-Z][\w&.'’-]*|&)(?: (?:[A-Z][\w&.'’-]*|&|of|de)){0,5})/))) {
    company = cleanCompany(m[1]);
  }
  return { person, company };
}

// ---- Main entry ------------------------------------------------------------------------------
export function classify({ title = '', summary = '' }, { requireInsurance = false } = {}) {
  const text = `${title}. ${summary}`;
  const titleInfo = bestTitle(title) || bestTitle(summary);
  const type = moveType(title) || moveType(summary);
  const relevant = !requireInsurance || isInsuranceRelated(text);
  const isMove = Boolean(titleInfo && type && relevant);
  const fromTitle = people(title);
  const fromSummary = people(summary);
  return {
    isMove,
    type,
    level: titleInfo?.level || null,
    role: titleInfo?.role || null,
    func: jobFunction(titleInfo?.role, text),
    sectors: sectors(text),
    person: fromTitle.person || fromSummary.person,
    company: fromTitle.company || fromSummary.company,
  };
}
