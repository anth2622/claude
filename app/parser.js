// Turns a free-form ingredient list into structured items.
// "2 lb chicken breast" -> { name: "chicken breast", quantity: 2, unit: "lb" }

const UNITS = {
  lb: 'lb', lbs: 'lb', pound: 'lb', pounds: 'lb',
  oz: 'oz', ounce: 'oz', ounces: 'oz',
  g: 'g', gram: 'g', grams: 'g', kg: 'kg', kilogram: 'kg', kilograms: 'kg',
  cup: 'cup', cups: 'cup',
  tbsp: 'tbsp', tablespoon: 'tbsp', tablespoons: 'tbsp',
  tsp: 'tsp', teaspoon: 'tsp', teaspoons: 'tsp',
  gal: 'gallon', gallon: 'gallon', gallons: 'gallon',
  qt: 'quart', quart: 'quart', quarts: 'quart',
  pint: 'pint', pints: 'pint',
  l: 'liter', liter: 'liter', liters: 'liter', litre: 'liter', litres: 'liter',
  ml: 'ml',
  dozen: 'dozen',
  can: 'can', cans: 'can', jar: 'jar', jars: 'jar',
  bag: 'bag', bags: 'bag', box: 'box', boxes: 'box',
  bunch: 'bunch', bunches: 'bunch', head: 'head', heads: 'head',
  pack: 'package', packs: 'package', package: 'package', packages: 'package', pkg: 'package',
  bottle: 'bottle', bottles: 'bottle', carton: 'carton', cartons: 'carton',
  loaf: 'loaf', loaves: 'loaf', clove: 'clove', cloves: 'clove',
  ct: 'each', count: 'each', each: 'each', ea: 'each',
};

// Units you buy one-for-one (2 cans -> buy 2). Weight/volume units mean "one package".
const COUNTABLE = new Set(['each', 'can', 'jar', 'bag', 'box', 'bunch', 'head', 'package',
  'bottle', 'carton', 'loaf', 'gallon', 'dozen']);

const UNICODE_FRACTIONS = { '½': 0.5, '¼': 0.25, '¾': 0.75, '⅓': 1 / 3, '⅔': 2 / 3, '⅛': 0.125 };

function parseNumber(tok) {
  if (!tok) return null;
  if (UNICODE_FRACTIONS[tok] != null) return UNICODE_FRACTIONS[tok];
  const uni = tok.match(/^(\d+)([½¼¾⅓⅔⅛])$/);
  if (uni) return Number(uni[1]) + UNICODE_FRACTIONS[uni[2]];
  const frac = tok.match(/^(\d+)\/(\d+)$/);
  if (frac && Number(frac[2]) !== 0) return Number(frac[1]) / Number(frac[2]);
  if (/^\d+(\.\d+)?$/.test(tok)) return Number(tok);
  return null;
}

let nextId = 1;

export function parseLine(raw) {
  let line = raw.trim()
    .replace(/^[-*•·▢☐□✓✔]+\s*/, '')     // bullets / checkboxes
    .replace(/^\d+[.)]\s+/, '')           // "1. milk" list numbering
    .replace(/\s+/g, ' ');
  if (!line) return null;

  let quantity = null;
  let unit = null;

  // "milk x2" / "milk ×2"
  const suffix = line.match(/\s*[x×]\s*(\d+)$/i);
  if (suffix) { quantity = Number(suffix[1]); line = line.slice(0, suffix.index); }

  let tokens = line.split(' ');
  // "2x milk"
  const prefixX = tokens[0].match(/^(\d+)[x×]$/i);
  if (prefixX) { quantity = Number(prefixX[1]); tokens.shift(); }

  if (quantity == null) {
    const first = parseNumber(tokens[0]);
    if (first != null) {
      quantity = first; tokens.shift();
      const second = parseNumber(tokens[0]);           // "1 1/2"
      if (second != null && second < 1) { quantity += second; tokens.shift(); }
    } else {
      // "2lb" glued to the unit
      const glued = (tokens[0] || '').match(/^(\d+(?:\.\d+)?)([a-z]+)$/i);
      if (glued && UNITS[glued[2].toLowerCase()]) {
        quantity = Number(glued[1]); unit = UNITS[glued[2].toLowerCase()]; tokens.shift();
      }
    }
  }

  if (!unit && tokens.length > 1) {
    const u = UNITS[tokens[0].toLowerCase().replace(/\.$/, '')];
    if (u) { unit = u; tokens.shift(); }
  }
  if (tokens[0] && tokens[0].toLowerCase() === 'of') tokens.shift();

  const name = tokens.join(' ').replace(/[,;]+$/, '').trim();
  if (!name) return null;

  return { id: `i${Date.now().toString(36)}${nextId++}`, raw: raw.trim(), name, quantity: quantity ?? 1, unit };
}

export function parseList(text) {
  return text.split(/\r?\n|,(?![^(]*\))(?=\s*\d)/).map(parseLine).filter(Boolean);
}

// How many store units to put in the cart for an item.
export function buyCount(item) {
  if (item.buyQty != null) return item.buyQty;
  if (!item.unit || COUNTABLE.has(item.unit)) return Math.max(1, Math.ceil(item.quantity));
  return 1;
}

export function describe(item) {
  const q = Number.isInteger(item.quantity) ? item.quantity : Number(item.quantity.toFixed(2));
  return item.unit ? `${q} ${item.unit} ${item.name}` : (q !== 1 ? `${q} × ${item.name}` : item.name);
}
