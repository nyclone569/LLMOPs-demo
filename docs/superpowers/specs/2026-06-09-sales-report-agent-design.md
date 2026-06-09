# Sales Report Agent Design

Date: 2026-06-09
Status: Draft for review

## Goal

Build a secure v1 sales report workflow that lets a user request `sales today` from Open WebUI without exposing PostgreSQL directly to the chat interface or sending business data to public LLM providers.

The v1 scope is intentionally narrow:
- One report: `sales today`
- One fixed business timezone: `Asia/Bangkok`
- One local model for summarization: Ollama
- No weekly reports
- No graphs
- No free-form SQL
- No public-model fallback

## Non-Goals

- Natural-language database exploration
- User-generated SQL
- Public-model summarization
- Historical trend analysis
- Dashboards or chart rendering
- Multi-agent orchestration

## Recommended Architecture

Use a separate backend service for reporting and keep Open WebUI as the UI entry point.

```text
Open WebUI
  -> Pipe Function or equivalent adapter
  -> report-service
  -> PostgreSQL
  -> report-service
  -> Ollama local API
  -> Open WebUI response
```

### Responsibilities

#### Open WebUI

- Exposes the user entry point for the report flow
- For v1, should integrate via a Pipe Function or equivalent server-side adapter, not only a slash prompt
- Passes authenticated user context and request metadata to the backend
- Does not run SQL
- Does not hold PostgreSQL reporting logic

#### Report Service

- Owns authorization checks for report access
- Owns business rules for `sales today`
- Uses fixed SQL or a stored procedure
- Uses a read-only PostgreSQL reporting account
- Produces a strict JSON report payload
- Calls Ollama with only the approved structured payload
- Returns the final response to Open WebUI

#### PostgreSQL

- Stores the sales data
- Is the source of truth for the report
- Is never queried directly by Open WebUI users
- Is never exposed to Ollama

#### Ollama

- Runs fully inside the server boundary
- Receives only structured report output from the report service
- Produces a short natural-language summary
- Has no database credentials and no tool autonomy in v1

## Why This Architecture

This design is preferred over direct Open WebUI SQL access or a multi-agent system because it creates a clear security boundary around the database while keeping the implementation small and auditable.

Benefits:
- Strong separation between UI, business logic, and data access
- Easier to audit than model-driven tool use
- Keeps business data inside the server
- Makes the SQL/report logic testable without involving the model
- Leaves room to add weekly reports and charts later without changing the trust boundary

## Integration Shape

### Open WebUI Integration

For v1, the entry point should be an Open WebUI Pipe Function or equivalent backend-aware integration, not just a slash prompt shortcut.

Reason:
- Prompt shortcuts are not enough for secure execution logic
- The workflow needs authorization checks, fixed query execution, and structured backend processing

The Pipe Function should remain thin:
- Accept the user request
- Call the report service
- Return the service response to the chat

It should not contain the core SQL/business logic unless building a throwaway prototype.

### Model Routing

The sales report path must not use public-provider aliases such as `fast-chat`, `coding-assistant`, or any LiteLLM route with public fallback.

Preferred v1 choice:
- Call Ollama directly from the report service

Acceptable alternative:
- Use a dedicated private-only route with no public fallback and restricted logging

The current LiteLLM config in the repository includes:
- Public provider aliases
- Fallback chains
- Langfuse callbacks

That is appropriate for general chat, but not for a sensitive reporting path without additional isolation.

## Data Model

Use a minimal transactional schema instead of a single summary table so the design can support future weekly reporting and product breakdowns.

### `orders`

- `id`
- `ordered_at`
- `status`
- `gross_amount`
- `discount_amount`
- `refund_amount`
- `net_amount`

### `order_items`

- `id`
- `order_id`
- `product_name`
- `quantity`
- `unit_price`
- `line_total`

This schema is enough for v1 and can later support:
- top products
- daily/weekly aggregations
- charts
- branch or category breakdowns if new dimensions are added

## Business Rules

The v1 report should use strict, deterministic rules.

### Timezone

- Business timezone is fixed to `Asia/Bangkok`
- `today` means the current calendar day in `Asia/Bangkok`
- Use the inclusive start and exclusive end of the day for filtering:
  - start: `00:00:00`
  - next day start as the upper bound

### Included Statuses

- Include only `paid` and `completed`

### Excluded Statuses

- Exclude `pending`
- Exclude `failed`
- Exclude `cancelled`

### Metrics

- `gross_sales = SUM(gross_amount)` across included orders
- `net_sales = SUM(net_amount)` across included orders
- `order_count = COUNT(*)` across included orders
- `refund_count = COUNT(*)` where `refund_amount > 0` across included orders

## Report Output Contract

The report service should return a small, explicit JSON payload before summarization.

```json
{
  "date": "2026-06-09",
  "timezone": "Asia/Bangkok",
  "gross_sales": 125430.50,
  "net_sales": 121980.50,
  "order_count": 842,
  "refund_count": 12
}
```

This contract is intentionally minimal for v1.

Future fields can be added later, for example:
- `top_products`
- `sales_vs_yesterday`
- `refund_amount_total`

## Data Flow

1. A user requests the sales report from Open WebUI.
2. Open WebUI invokes the Pipe Function or equivalent adapter.
3. The adapter calls the report service with authenticated user context.
4. The report service validates authorization.
5. The report service computes the `Asia/Bangkok` date window.
6. The report service runs fixed SQL against PostgreSQL using the reporting account.
7. PostgreSQL returns the data required to compute the approved aggregates.
8. The report service builds the structured JSON payload.
9. The report service sends the payload to Ollama with a constrained summarization prompt.
10. Ollama returns a short natural-language summary.
11. The report service returns the final response to Open WebUI.

## Security Controls

### Database Access

- Use a dedicated read-only PostgreSQL user for reporting
- Limit that account to the required tables, views, or stored procedures only
- Prefer fixed SQL or a stored procedure over generated SQL

### Model Isolation

- Ollama is the only model used for this feature in v1
- Ollama must not receive database credentials
- Ollama must not choose what query to run
- Ollama must not receive raw order rows unless explicitly approved in a future revision

### Routing and Logging

- Do not allow this feature to fall back to public models
- Avoid sending this traffic through a generic public-capable route unless that route is explicitly isolated
- Restrict logging of prompts and responses for this path if business metrics are considered sensitive

### Authorization

- Report access must be checked before any query runs
- Unauthorized users must receive an access error without touching the report query path

## Failure Behavior

The system should fail safely and deterministically.

- If authorization fails, return an access-denied error
- If the database query fails, return an operational error and do not invent numbers
- If Ollama fails, return either:
  - the structured JSON as a fallback, or
  - a deterministic plain-text summary generated by the report service without AI

The system must never guess missing values.

## Sample Data Strategy

Use generated sample data for v1 rather than a Kaggle dataset.

Reasons:
- Matches the chosen schema directly
- Lets the team control edge cases such as refunds, cancellations, and time boundaries
- Avoids adapting the design to an external dataset
- Simplifies test setup and documentation

Later, the project can optionally add:
- anonymized real-like data
- larger datasets for load testing

## Why Not Multi-Agent

This problem does not need full multi-agent orchestration in v1.

Reasons:
- The hard part is security and controlled data access, not open-ended reasoning
- The reporting logic is deterministic
- More agents would add routing, permission, and observability complexity without clear benefit

The right abstraction for v1 is:
- one backend reporting service
- one local summarization model

## Testing Strategy

The reporting logic should be verified independently from the model.

Required test cases:
- correct totals for a normal same-day dataset
- zero-orders day
- excluded statuses do not affect totals
- refunded orders affect `refund_count`
- midnight boundary behavior in `Asia/Bangkok`
- unauthorized user path
- database failure path
- Ollama failure fallback path

## Implementation Guidance

Use ordinary backend libraries, not an agent SDK.

Suggested stack:
- Open WebUI Pipe Function for chat integration
- Python report service, preferably FastAPI
- PostgreSQL client such as `psycopg` or SQLAlchemy
- `httpx` or equivalent HTTP client for calling Ollama

This feature should be treated as deterministic backend logic with local AI summarization, not as an autonomous agent system.
