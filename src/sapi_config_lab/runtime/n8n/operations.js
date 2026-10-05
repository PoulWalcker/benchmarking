// Demo bindings. LLM operations below are deterministic stubs, not model calls.
function need(condition, message) { if (!condition) throw new Error(message); }
const operations = {
  'json.parse': ({text}) => ({value: JSON.parse(text)}),
  'report.evidence': ({narrative, evidence}) => ({
    final_answer: narrative + '\n\nRecorded operation receipts and final readback (authoritative results; failed operations remain failures):\n' + evidence.join('\n'),
  }),
  'invoices.validate': ({invoices}) => {
    need(Array.isArray(invoices) && invoices.length > 0, 'Invoices must be nonempty');
    need(new Set(invoices.map(x => x.id)).size === invoices.length, 'Duplicate invoice ID');
    const currency = invoices[0].currency;
    for (const x of invoices) {
      need(typeof x.id === 'string' && x.id.length > 0, 'Missing invoice ID');
      need(Number.isSafeInteger(x.amount_minor) && x.amount_minor >= 0, 'Invalid amount');
      need(typeof currency === 'string' && currency.length === 3 && x.currency === currency, 'Mixed/invalid currency');
    }
    return {invoices};
  },
  'invoices.sum': ({invoices}) => {
    const amount_minor = invoices.reduce((sum, x) => sum + x.amount_minor, 0);
    need(Number.isSafeInteger(amount_minor), 'Total exceeds safe integer range');
    return {amount_minor, currency: invoices[0].currency, count: invoices.length};
  },
  'invoices.report': ({total}) => ({total_minor: total.amount_minor, currency: total.currency, invoice_count: total.count}),
  'ticket.classify': ({ticket}) => {
    need(Number.isInteger(ticket.days_overdue) && ticket.days_overdue >= 0, 'Invalid overdue days');
    return {category: 'delivery', priority: ticket.days_overdue > 2 ? 'high' : 'normal'};
  },
  'ticket.escalation_draft': ({ticket}) => ({ticket_id: ticket.id, action: 'escalate', mode: 'draft'}),
  'ticket.normal_draft': ({ticket}) => ({ticket_id: ticket.id, action: 'normal_reply', mode: 'draft'}),
  'branch.select_one': ({escalated, normal}) => {
    const values = [escalated, normal].filter(x => x !== null);
    need(values.length === 1, 'Exactly one branch must produce a result');
    return values[0];
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
  'reply.generate': ({ticket, previous, feedback}) => ({
    text: feedback.length === 0 ? 'We are checking your order.' : 'We are checking order ' + (ticket.text.match(/A\d+/)?.[0] || 'unknown') + '.',
  }),
  'reply.check': ({draft, order_id, max_characters}) => {
    const errors = [];
    if (!draft.text.includes(order_id)) errors.push('Include the order ID');
    if (draft.text.length > max_characters) errors.push('Shorten the reply');
    return {pass: errors.length === 0, errors};
  },
  'digest.prepare': ({articles}) => {
    need(Array.isArray(articles) && articles.length > 0, 'No articles');
    need(new Set(articles.map(x => x.id)).size === articles.length, 'Duplicate article ID');
    return {articles};
  },
  'digest.summarize': ({articles}) => ({text: articles.map(x => x.title + ': ' + x.text).join('\n'), article_ids: articles.map(x => x.id)}),
  'digest.preview': ({summary}) => ({mode: 'preview', ...summary}),
};

