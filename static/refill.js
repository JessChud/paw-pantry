'use strict';
document.getElementById('refill').addEventListener('submit', (event) => {
  event.preventDefault();
  const amount = Number(document.getElementById('amount').value);
  const daily = Number(document.getElementById('daily').value);
  const lead = Number(document.getElementById('lead').value);
  const unit = document.getElementById('unit').value.trim();
  const result = document.getElementById('result');
  if (!Number.isFinite(amount) || !Number.isFinite(daily) || !Number.isFinite(lead) ||
      amount <= 0 || daily <= 0 || lead < 0 || !unit) {
    result.textContent = 'Enter positive amounts in the same unit and a non-negative lead time.';
    return;
  }
  const days = amount / daily;
  if (!Number.isFinite(days) || days > 36500) {
    result.textContent = 'Check the amounts and units: this estimate exceeds 100 years.';
    return;
  }
  const refill = days - lead;
  const format = (value) => value < 0.1 ? 'less than 0.1' : value.toFixed(1);
  result.textContent = `About ${format(days)} days of supply at ${daily} ${unit} per day. ` +
    (refill <= 0 ? 'Your shopping or delivery lead time has already begun; check the supply now.' :
      `Consider a refill in ${format(refill)} days, allowing ${lead} days for shopping or delivery.`);
});
