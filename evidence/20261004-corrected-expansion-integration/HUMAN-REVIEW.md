# Live text review packet

Status: pending human review. Automatic native, quotation, citation, and factual-anchor gates passed for the completed cases listed below; they do not establish exhaustive semantic correctness.

Review each digest and final brief against its exact fixture: all capabilities, audience and marketing channel must remain supported; article IDs must be valid; no invented benefits, omitted facts, stale facts from another case, or unsupported conclusions. For the normal-priority SC-04 case, confirm branch skipping and the deterministic route result separately from generated text.

This pending review does not change the bounded automatic evaluation outcome. Team-pilot entry requires the planned third accepted independent authoring attempt per task and recorded human review; neither is claimed here.

## bulletin-market-brief / base-bulletins

Native workflow `2835baac84374966`, execution `1`. Automatic gate: `accepted`. Human judgment: pending.

Exact source inputs:

```json
{
  "articles": [
    {
      "id": "319929BB5149-BL-1",
      "title": "Offline inventory",
      "text": "Fixture 319929BB5149: The product supports offline inventory."
    },
    {
      "id": "319929BB5149-BL-2",
      "title": "CSV export",
      "text": "Fixture 319929BB5149: The product supports CSV export."
    }
  ],
  "marketing_material": "Fixture 319929BB5149: The service targets rural cooperatives through printed catalogues."
}
```

Exact returned output:

```json
{
  "digest": {
    "mode": "preview",
    "text": "The product supports offline inventory and CSV export.",
    "article_ids": [
      "319929BB5149-BL-1",
      "319929BB5149-BL-2"
    ]
  },
  "brief": {
    "report": "The product supports offline inventory and CSV export. The service targets rural cooperatives through printed catalogues.",
    "evidence": [
      "The product supports offline inventory and CSV export.",
      "The service targets rural cooperatives through printed catalogues."
    ]
  }
}
```

Raw trial: `sc03-live/jobs/live-bulletin-market-brief-base-bulletins/bulletin-market-brief__9EvUws2/result.json`.

## bulletin-market-brief / alternate-bulletins

Native workflow `dcd3d1ab840a42af`, execution `1`. Automatic gate: `accepted`. Human judgment: pending.

Exact source inputs:

```json
{
  "articles": [
    {
      "id": "319929BB5149-ALT-7",
      "title": "Appointment booking",
      "text": "Fixture 319929BB5149: The product supports appointment booking."
    },
    {
      "id": "319929BB5149-ALT-9",
      "title": "SMS reminders",
      "text": "Fixture 319929BB5149: The product supports SMS reminders."
    }
  ],
  "marketing_material": "Fixture 319929BB5149: The service targets dental clinics through radio adverts."
}
```

Exact returned output:

```json
{
  "digest": {
    "mode": "preview",
    "text": "The product supports appointment booking and SMS reminders.",
    "article_ids": [
      "319929BB5149-ALT-7",
      "319929BB5149-ALT-9"
    ]
  },
  "brief": {
    "report": "The product supports appointment booking and SMS reminders. The service targets dental clinics through radio adverts.",
    "evidence": [
      "The product supports appointment booking and SMS reminders.",
      "The service targets dental clinics through radio adverts."
    ]
  }
}
```

Raw trial: `sc03-live/jobs/live-bulletin-market-brief-alternate-bulletins/bulletin-market-brief__zF2bD6A/result.json`.
## priority-support-brief / high-brief

Native workflow `f224e6b8948b4b3d`, execution `1`. Automatic gate: `accepted`. Human judgment: pending where generated text exists.

Exact source inputs:

```json
{
  "ticket": {
    "id": "4E55FBF215B8-P-72",
    "text": "Fixture 4E55FBF215B8: Delivery is three days late.",
    "days_overdue": 3
  },
  "product_material": "Fixture 4E55FBF215B8: The product supports barcode scanning and offline inventory.",
  "marketing_material": "Fixture 4E55FBF215B8: The service targets local shops through partner referrals."
}
```

Exact returned output:

```json
{
  "action": {
    "ticket_id": "4E55FBF215B8-P-72",
    "action": "escalate",
    "mode": "draft"
  },
  "brief": {
    "report": "The product supports barcode scanning and offline inventory. The service targets local shops through partner referrals.",
    "evidence": [
      "The product supports barcode scanning and offline inventory.",
      "The service targets local shops through partner referrals."
    ]
  }
}
```

Raw trial: `sc04-live/jobs/live-priority-support-brief-high-brief/priority-support-brief__sxBSFrw/result.json`.

## priority-support-brief / normal-empty

Native workflow `781d3679d73c4acd`, execution `1`. Automatic gate: `accepted`. Human judgment: pending where generated text exists.

Exact source inputs:

```json
{
  "ticket": {
    "id": "4E55FBF215B8-P-72",
    "text": "Fixture 4E55FBF215B8: Delivery is three days late.",
    "days_overdue": 2
  },
  "product_material": "",
  "marketing_material": ""
}
```

Exact returned output:

```json
{
  "action": {
    "ticket_id": "4E55FBF215B8-P-72",
    "action": "normal_reply",
    "mode": "draft"
  },
  "brief": null
}
```

Raw trial: `sc04-live/jobs/live-priority-support-brief-normal-empty/priority-support-brief__pn7jSwH/result.json`.
