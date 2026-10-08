function need(condition, message) { if (!condition) throw new Error(message); }
const operations = {
  'probe.advance': ({previous}) => ({value: previous === null ? 1 : previous.value + 1}),
  'probe.check': ({draft, target, ceiling}) => {
    const errors = [];
    if (draft.value < target) errors.push('Increase value');
    if (draft.value > ceiling) errors.push('Value exceeds ceiling');
    return {pass: errors.length === 0, errors};
  },
};
