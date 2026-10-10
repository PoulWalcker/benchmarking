// These local operations are trusted by the task. LLM uses this only in stub mode.
function need(condition, message) { if (!condition) throw new Error(message); }
const operations = {
  'ticket.classify': ({ticket}) => {
    need(typeof ticket.id === 'string' && ticket.id.length > 0, 'Missing ticket ID');
    need(Number.isInteger(ticket.days_overdue) && ticket.days_overdue >= 0, 'Invalid overdue days');
    return {category: 'delivery', priority: ticket.days_overdue > 2 ? 'high' : 'normal'};
  },
  'ticket.escalation_draft': ({ticket}) => ({ticket_id: ticket.id, action: 'escalate', mode: 'draft'}),
  'ticket.normal_draft': ({ticket}) => ({ticket_id: ticket.id, action: 'normal_reply', mode: 'draft'}),
  'branch.select_one': ({escalated, normal}) => {
    const candidates = [escalated, normal].filter(value => value !== null);
    need(candidates.length === 1, 'Exactly one routing branch must complete');
    return candidates[0];
  },
};
