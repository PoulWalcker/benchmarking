// Unpaid controls: these are deterministic LLM substitutes, NOT model inference.
function need(ok, why) { if (!ok) throw new Error(why); }
const operations = {
  'research.validate': ({material}) => {
    need(typeof material === 'string' && material.trim().length > 0, 'Material must be nonempty');
    return {material};
  },
  'research.product': ({material}) => {
    need(typeof material === 'string' && material.length > 0, 'Missing product material');
    return {summary: 'Product: ' + material, evidence: material};
  },
  'research.marketing': ({material}) => {
    need(typeof material === 'string' && material.length > 0, 'Missing marketing material');
    return {summary: 'Marketing: ' + material, evidence: material};
  },
  'research.combine': ({product, marketing}) => ({product, marketing}),
  'research.write': ({brief}) => ({
    report: brief.product.summary + '\n\n' + brief.marketing.summary,
    evidence: [brief.product.evidence, brief.marketing.evidence],
  }),
};
