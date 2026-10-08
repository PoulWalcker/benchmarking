function need(condition, message) { if (!condition) throw new Error(message); }
const operations = {
  'beacon.complete': ({sample}) => {
    const packet = JSON.parse(sample);
    need(packet.ok === true, 'Beacon sample failed');
    return {calibration: packet.value.angle, station: packet.value.station, seal: packet.value.seal};
  }
};
