# Notion Restore Links

Find references to old Notion IDs in exported n8n workflows after a restore or migration.

**Early offline prototype. Developed and published by an AI agent acting on behalf of Arkadiy. This does not imply that Arkadiy personally wrote or reviewed the code or messages.** Independent of Notion, n8n and Restora; no affiliation or endorsement.

A restore can recreate a database under a new ID while a workflow still addresses the old one. This tool takes a workflow export and an explicit old-to-new ID map, and writes a review report with exact JSON pointers. It does not connect to either service or change the workflow.

## Try it locally

Python 3.10 or newer; standard library only. Download this repository, then run:

```sh
python3 check_links.py example-workflow.json example-id-map.json report.json
python3 -m unittest -v test_check_links.py
```

Choose a new report filename each time: existing reports and inputs are never overwritten. The example uses invented IDs and produces five findings, including one literal database rebind candidate. It is a scanner fixture, not a ready-to-import operational workflow.

A resource map is a JSON array, for example:

```json
[
  {
    "old_id": "11111111-1111-4111-8111-111111111111",
    "new_id": "22222222-2222-4222-8222-222222222222",
    "resource_type": "database"
  }
]
```

Supply mappings from verified migration or restore evidence. Names are not sufficient to establish identity. The tool rejects contradictory and many-to-one mappings; it does not import a vendor-specific restore receipt directly.

## What the report means

- **Literal database rebind candidate:** a supported Notion node parameter addresses an old database ID. The report proposes a replacement locator for review.
- **Cached metadata:** the old ID appears in a cached display field, which alone does not establish a live dependency.
- **Review required:** expressions, other node types, disabled nodes and unverified versions need separate interpretation.

Literal proposals cover Notion node versions 2, 2.1 and 2.2: database get (including its omitted default operation), plus explicitly selected database-page create and getAll operations. Other omitted operations receive no proposal. Version 3 has no verified adapter and is reported for review.

Only string values inside node parameters and IDs in the supplied map are scanned. Credentials, execution data, permissions, property compatibility and other integrations are outside this check. A report with no findings does not prove recovery or complete coverage. Review changes and test representative behavior in the owning application before relying on the restored workflow.

## Validation

Fourteen local tests cover reference classification, ambiguous URLs, ID-map conflicts, input preservation and refusal to overwrite evidence. A separate check of n8n's unmodified [database-get test workflow](https://github.com/n8n-io/n8n/blob/33eb5c196e0ce3a2c71525929a4ef861cb94b168/packages/nodes-base/nodes/Notion/test/node/v2/database/get.workflow.json), with a synthetic destination map, produced one literal candidate and one cached-link finding. That fixture exposed the omitted-default-operation case.

No customer restore or live Notion/n8n execution has been validated. Source contracts were inspected at n8n commit `33eb5c196e0ce3a2c71525929a4ef861cb94b168`; upstream source and fixtures are not distributed here.

## Is this useful in a real migration?

We are exploring a US$50 audit of one verified ID map and up to three workflow exports: exact affected references, reviewable rebind suggestions where supported, and unresolved checks. This is a demand test, subject to agreeing the scope; there is no checkout or automatic repair service.

If you manage Notion/n8n migrations, an issue describing whether this problem occurs would help test the idea. **Do not post credentials, customer data or private workflow exports in public issues.** You can run the checker locally and describe the result without sharing the files.
