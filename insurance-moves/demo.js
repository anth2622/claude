// Fictional sample moves so you can try the app before the collector has run. Every company and
// person here is made up.
const d = n => new Date(Date.now() - n * 864e5).toISOString().slice(0, 10);
const m = (n, person, company, role, level, type, func, sectors, title) => ({
  id: `demo-${n}-${person.replace(/\W/g, '')}`, date: d(n), title, summary: 'Sample entry (demo data, not a real announcement).',
  url: '', source: 'Demo data', sourceType: 'demo', person, company, role, level, type, func, sectors, links: [],
});
export const DEMO = [
  m(1, 'Jane Doe', 'Northbridge Mutual', 'Chief Underwriting Officer', 'C-suite', 'Appointment', 'Underwriting', ['P&C'], 'Northbridge Mutual appoints Jane Doe as chief underwriting officer'),
  m(2, 'Sam Ortiz', 'Example MGA', 'Managing Director', 'President / MD', 'Appointment', 'General management', ['Specialty / E&S / Lloyd\'s'], 'Sam Ortiz joins Example MGA as managing director'),
  m(3, 'Peter Brown', 'Contoso Specialty', 'CFO', 'C-suite', 'Departure', 'Finance / Investments', ['Specialty / E&S / Lloyd\'s'], 'Contoso Specialty CFO Peter Brown steps down'),
  m(5, 'Alex Kim', 'Fabrikam Insurance', 'Executive Vice President, Claims', 'EVP / SVP', 'Promotion', 'Claims', ['P&C'], 'Fabrikam Insurance promotes Alex Kim to EVP, claims'),
  m(6, 'Chris Wong', 'Woodgrove Re', 'SVP, Cyber Reinsurance', 'EVP / SVP', 'Appointment', 'Underwriting', ['Reinsurance'], 'Woodgrove Re hires Chris Wong as SVP, cyber reinsurance'),
  m(9, 'John Smith', 'Acme Life', 'CEO', 'C-suite', 'Succession', 'General management', ['Life & Annuity'], 'Acme Life CEO John Smith to retire; Mary Lee named successor'),
  m(12, 'Ruth Park', 'Tailspin Health Plan', 'Board of Directors', 'Board', 'Appointment', null, ['Health'], 'Tailspin Health Plan elects Ruth Park to board'),
  m(15, 'Dana White', 'Northbridge Mutual', 'Chief Financial Officer', 'C-suite', 'Appointment', 'Finance / Investments', ['P&C'], 'Northbridge Mutual names Dana White CFO'),
  m(20, 'Lee Grant', 'Example Brokerage', 'Head of Construction', 'Head / Director', 'Appointment', 'Distribution / Sales', ['Broker / Agency'], 'Lee Grant joins Example Brokerage as head of construction'),
  m(26, 'Morgan Ellis', 'Woodgrove Re', 'Chief Risk Officer', 'C-suite', 'Retirement', 'Risk', ['Reinsurance'], 'Woodgrove Re chief risk officer Morgan Ellis to retire'),
  m(33, 'Priya Shah', 'Lumen InsurTech', 'Chief Technology Officer', 'C-suite', 'Appointment', 'Technology / Data', ['InsurTech'], 'Lumen InsurTech taps Priya Shah as CTO'),
  m(41, 'Tom Reyes', 'Example Brokerage', 'President of US Retail', 'President / MD', 'Promotion', 'General management', ['Broker / Agency'], 'Example Brokerage promotes Tom Reyes to president of US retail'),
  m(55, 'Ava Chen', 'Contoso Specialty', 'Vice President, Marine', 'VP', 'Appointment', 'Underwriting', ['Specialty / E&S / Lloyd\'s'], 'Contoso Specialty hires Ava Chen as VP, marine'),
  m(70, 'Noah Field', 'Acme Life', 'Chief Actuary', 'C-suite', 'Departure', 'Actuarial', ['Life & Annuity'], 'Acme Life chief actuary Noah Field departs'),
];
