# Can Microsoft Graph read our coordinators' event titles?

**Research report and decision plan.** ESD Lab visit-scheduling engine, v3.
17 August 2026. Companion to [`ESD-Visit-Scheduling-v3-SPEC.md`](ESD-Visit-Scheduling-v3-SPEC.md)
and [`ESD-Calendar-Ingestion-PROMPT-AND-STRATEGY.md`](ESD-Calendar-Ingestion-PROMPT-AND-STRATEGY.md).

---

## 1. The question that was actually asked

From the meeting, three separate questions were tangled together. They have
different answers, and separating them is most of the work:

| # | Question as asked | What it really asks |
|---|---|---|
| **Q1** | "Can we confirm there is absolutely no function that would allow you to read the titles from those other calendars?" | Does the *API surface* contain such a function? |
| **Q2** | "If that function does not exist, and all it can ever see is busy or not, then that is way better." | Can *our app*, as configured, be made structurally incapable of reading titles? |
| **Q3** | "It shows nothing... but I saw free, tentative and busy. So it'd have to be able to discern that between 8:30 and 5 I'm free." | Can the API tell an explicitly-free block apart from an empty calendar? |

**Short answers.**

> **Q1 — No. Such functions exist, and we should say so plainly.**
> `GET /users/{id}/events` and `getSchedule` both return `subject`. Claiming
> otherwise to the group would be wrong and would collapse the moment anyone
> reads the docs.
>
> **Q2 — Yes, but only in one of the two possible architectures.** Under
> **delegated** authentication with the tenant's default free/busy sharing,
> Exchange withholds `subject` at the server. Under **application-only**
> authentication it does not, and — critically — **cannot be reduced to
> free/busy at all**, because Microsoft does not publish a calendar app role
> below full `Calendars.Read`.
>
> **Q3 — Yes.** `getSchedule` returns a `workingHours` object alongside the
> free/busy view, and `free` is a distinct status from an absent entry. The
> 8:30-to-5 case raised in the meeting is directly answerable.

The rest of this document is the evidence, the correction of a wrong mapping we
were about to build on, and the exact IT ticket to file.

---

## 2. Evidence

### 2.1 `getSchedule` does return subject and location

The [getSchedule reference](https://learn.microsoft.com/en-us/graph/api/calendar-getschedule)
gives this response for `adelev@contoso.com`:

```json
{
  "isPrivate": false,
  "status": "busy",
  "subject": "Let's go for lunch",
  "location": "Harry's Bar",
  "start": { "dateTime": "2019-03-15T12:00:00.0000000" },
  "end":   { "dateTime": "2019-03-15T14:00:00.0000000" }
}
```

The [`scheduleItem` resource](https://learn.microsoft.com/en-us/graph/api/resources/scheduleitem)
confirms the field list: `end`, **`isPrivate`**, **`location`**, `start`,
`status`, **`subject`**. Every one of the three sensitive fields is marked
*Optional*, which is the documentation's way of saying "returned when the caller
is entitled to it."

**This is the finding that matters, and it is the one the PI was right to
worry about.** So the PI's instinct — that the permission description sounded
like it could read titles — was correct.

### 2.2 The same response withholds subject for the other user

In that *identical* API call, the second mailbox `meganb@contoso.com` returns
`scheduleItems` with **no `subject` key at all**:

```json
{
  "status": "busy",
  "start": { "dateTime": "2019-03-15T08:30:00.0000000" },
  "end":   { "dateTime": "2019-03-15T09:30:00.0000000" }
}
```

One request. One token. One permission scope. Two mailboxes, two different
detail levels. The variable is therefore **not** the OAuth scope — it is the
**per-mailbox calendar sharing permission in Exchange**.

### 2.3 The sharing level is the real control plane

Exchange calendar sharing levels, and what each releases:

| Sharing level | Releases | Subject visible? |
|---|---|---|
| **AvailabilityOnly** (tenant default) | free / busy / tentative / OOF and the time slot | **No** |
| **LimitedDetails** | the above **plus title and location** | **Yes** |
| **Reviewer / FullDetails** | full event detail | Yes |
| **Editor / Delegate** | full detail plus write | Yes |

`AvailabilityOnly` "allows other users to see a graphic representation of when
someone is available... doesn't allow you to see details," while
`LimitedDetails` "allows people to see the time slot reserved, the title,
location, and its time status." Sources:
[Office365ITPros](https://office365itpros.com/2021/09/21/exchange-view-calendar-information/),
[ALI TAJRAN](https://www.alitajran.com/set-default-calendar-permissions-for-all-users-powershell/),
[Windows OS Hub](https://woshub.com/manage-calendar-permissions-exchange-microsoft-365/).

This is exactly the behaviour observed in the meeting: opening a colleague's
calendar in Outlook shows only "Busy". Outlook and Graph are reading the same
Exchange free/busy service, under the same sharing rules.

### 2.4 The permission description the team read is accurate — and worse than it sounds

From the [permissions reference](https://learn.microsoft.com/en-us/graph/permissions-reference):

| Permission | Type | Description | Reads subject? |
|---|---|---|---|
| `Calendars.ReadBasic` | Delegated | "read events in user calendars, except for properties such as body, attachments, and extensions" | **Yes** |
| `Calendars.ReadBasic.All` | Application | same, "without a signed-in user" | **Yes** |
| `Calendars.Read` | Both | "read events of all calendars" | Yes, plus body |
| `Calendars.Read.Shared` | Delegated | "read events in all calendars that the user can access, including delegate and shared calendars" | Subject to sharing level |
| `Calendars.ReadWrite` | Both | "create, read, update, **and delete** events" | Yes, and can modify |

The team's reading was right: **"except body, attachments, extensions" does not
exclude the subject line.** `ReadBasic` is about excluding the *body*, not the
*title*. The scope name is misleading and the concern was well-founded.

### 2.5 The decisive finding: app-only cannot be reduced to free/busy

Exchange Online RBAC for Applications is the modern way to scope an app-only
identity to specific mailboxes. Its
[published role table](https://learn.microsoft.com/en-us/exchange/permissions-exo/application-rbac)
lists, for calendars, exactly two roles:

- `Application Calendars.Read` — "read events of all calendars without a signed-in user"
- `Application Calendars.ReadWrite` — "create, read, update, and delete events of all calendars"

**There is no `Application Calendars.ReadBasic` and no free/busy-only role.**
Mail has a `ReadBasic` variant; calendars do not.

The consequence is sharp and worth stating to the group in one sentence:

> In the app-only architecture, the *least* calendar access Microsoft will sell
> us is full read of every event, including subject and body. Scoping restricts
> **which mailboxes**, never **which fields**.

Two further gotchas in the same doc:
- Entra ID grants and Exchange RBAC grants are **additive** ("a union operation").
  Scoping in Exchange achieves nothing unless the tenant-wide Entra consent is
  removed first.
- "Exclusive management scopes don't restrict app access."

### 2.6 The mapping we were about to build on is wrong

Copilot suggested to the team: *"Busy code is zero, tentative one and available
is three."* The
[`scheduleInformation` reference](https://learn.microsoft.com/en-us/graph/api/resources/scheduleinformation)
states the actual `availabilityView` encoding:

| Digit | Meaning |
|---|---|
| `0` | **free** — or working elsewhere |
| `1` | tentative |
| `2` | **busy** |
| `3` | out of office |

> "**Note:** Working elsewhere is set to `0` instead of `4` for backward compatibility."

Copilot's mapping inverts free and busy. Had we built the coordinator-availability
grid on it, the scheduler would have **booked visits into exactly the slots
where people were busy**, and the error would have looked like a plausible
scheduling result rather than a bug. Flag this to the group; it is the single
most expensive mistake avoided this week.

Note also that `workingElsewhere` collapses into `0` in `availabilityView` but
stays distinct in `scheduleItems[].status`. If the lab wants "working elsewhere"
treated as unavailable, we must read the item status, not the view string.

### 2.7 Free blocks versus empty calendar

`getSchedule` returns, per person:

```json
"workingHours": {
  "daysOfWeek": ["monday","tuesday","wednesday","thursday","friday"],
  "startTime": "08:00:00.0000000",
  "endTime": "17:00:00.0000000",
  "timeZone": { "name": "Pacific Standard Time" }
}
```

So all three cases the team raised are distinguishable:

| Situation | How it appears |
|---|---|
| Grad student blocks 1–3 PM as "Free" | `scheduleItems` entry with `status: "free"`; `availabilityView` digit `0` |
| Nothing scheduled, inside working hours | no `scheduleItems` entry; digit `0`; **inside** `workingHours` |
| Nothing scheduled, outside working hours | no entry; digit `0`; **outside** `workingHours` |

The naive read — "digit 0 means available" — would offer coordinators visits at
7 PM. The rule to implement is **`digit == 0` AND inside `workingHours`**, which
is precisely the discrimination asked for in the meeting. This is also a genuine
advantage over the OCR/PDF route, which cannot see working hours at all.

---

## 3. The three candidate architectures

| | **A. Delegated** | **B. App-only + RBAC scope** | **C. OCR / PDF** |
|---|---|---|---|
| Auth | Coordinator (or scheduler) signs in; app acts as them | App runs headless with its own identity | No auth; manual export |
| Calendar scope | `Calendars.Read.Shared` | `Application Calendars.Read`, scoped to a mailbox group | — |
| **Can it read titles?** | **No** — Exchange withholds `subject` under AvailabilityOnly | **Yes** — no lesser role exists | No |
| Can it modify calendars? | No (read scopes only) | No (read role only), but ReadWrite is one config change away | No |
| Blast radius if token leaks | Only what that one person could already see | Every mailbox in scope, full event detail | None |
| Per-person setup | Sharing already works org-wide by default | Admin does it once | Manual export each time |
| End times / intervals | Yes | Yes | **No** — month view has none |
| Working hours | Yes | Yes | No |
| Unattended / scheduled runs | Needs a service account or refresh token | Yes, natively | No |
| Reliability | High | High | Low — OCR error, 7-row truncation |
| **Meets the PI's bar** | **Yes** | No | Yes, but unusable |

### Recommendation: **Architecture A, delegated.**

It is the only option that satisfies the constraint as the PI stated it —
*"all it can ever see, no matter how hard you try, is busy or not"* — while
still giving us real end times and working hours.

The reasoning is worth stating precisely, because it is not "we promise not to
read titles". It is:

> Under delegated auth, the app inherits the signed-in person's view. The
> coordinators have shared free/busy only. Exchange therefore never puts
> `subject` in the response. Reading titles is not *disallowed by policy* — it
> is *absent from the payload*. Nothing in our code, and no change to our code,
> can retrieve a field the server did not send.

That is the property worth confirming, and it is confirmable by experiment
(§5). It also answers the "micromanaging who shares what with whom" pain point
raised at the end of the meeting: **nobody has to share anything.** Default
org-wide free/busy already works; there is no per-coordinator share, no
permission choice, no re-doing it when someone changes jobs.

### Why not B

Two independent reasons, either sufficient:

1. **No least-privilege option exists.** `Application Calendars.Read` reads
   subject *and body* for every in-scope mailbox. We would be holding more
   access than the role requires — the exact concern raised in the meeting
   ("I potentially have access to more information than I should in my role").
2. **`Calendars.ReadWrite` is one dropdown away.** The meeting flagged that
   Graph can modify calendars. Under A, the write path does not exist at all.

### On C (OCR)

Keep it as documented fallback only. The August export analysis found no end
times, silent 7-row truncation on 17 of 33 day cells, and truncation biased
toward *later* entries — so afternoons look free precisely when they are not.
Details in the ingestion strategy doc. It is strictly worse than A on every
axis except "requires no IT involvement."

---

## 4. The IT ticket

Namit offered to raise one. Suggested text — the questions are phrased so a
"yes" is verifiable and a "no" is actionable.

> **Subject:** Graph API free/busy access for a scheduling tool — confirming
> event titles cannot be read
>
> The Early Social Development Lab is building an internal tool that reads
> coordinator availability to schedule research home visits. We want it to see
> **free/busy only** and to be structurally incapable of reading event subjects.
> Please confirm or correct the following.
>
> 1. Our intended design is **delegated** authentication (`Calendars.Read.Shared`),
>    where the app acts as the signed-in user. Is that supported for an internal
>    line-of-business app in our tenant?
> 2. What is the tenant's **default calendar sharing level** between staff —
>    `AvailabilityOnly`, or something more permissive? Please confirm with
>    `Get-MailboxFolderPermission <user>:\Calendar -User Default`.
> 3. Under that default, we expect `POST /me/calendar/getSchedule` to return
>    `scheduleItems` **without** the `subject` or `location` fields for other
>    staff. Can you confirm, ideally by running the call for two staff mailboxes?
> 4. Are there existing sharing grants above `AvailabilityOnly` between lab staff
>    that would cause titles to be returned for some people and not others?
> 5. Can you confirm we will **not** be granted `Calendars.ReadWrite` or
>    `Calendars.Read` (application), and that no application-permission consent
>    exists for this app registration?
> 6. Is there any tenant policy that would let us block `subject` at the service
>    level as defence in depth, independent of sharing level?
> 7. If the lab later wants unattended nightly runs, what is the approved pattern
>    — a service account with delegated consent, or app-only with Exchange RBAC?
>    If app-only, note that Microsoft publishes no calendar role below
>    `Application Calendars.Read`, which reads subject and body; we would want to
>    discuss alternatives.
>
> We are not requesting access to event contents, attachments, or bodies at any
> point.

---

## 5. Verification protocol — "try to break it"

The PI asked to try to break it and to have someone else confirm it cannot be
broken easily. That is the right instinct and it deserves a written protocol
rather than a demo. Run every test in Graph Explorer against **real lab
mailboxes**, screenshot each result, and file them.

| # | Test | Expected under Architecture A | If it fails |
|---|---|---|---|
| T1 | `POST /me/calendar/getSchedule` for 6 coordinators, 1 week | `availabilityView` present; **no `subject`, no `location`** on any item | A sharing grant is above AvailabilityOnly — find it with `Get-MailboxFolderPermission` |
| T2 | `GET /users/{coordinator}/events` | **403 / ErrorAccessDenied** | The app holds more than delegated free/busy — stop and revoke |
| T3 | `GET /users/{coordinator}/calendar/events?$select=subject` | 403 | as T2 |
| T4 | `GET /me/calendars` then read another person's calendar by id | 403 or free/busy only | as T2 |
| T5 | `POST /users/{coordinator}/events` (create) | **403** — proves no write path | Write scope was consented; remove it |
| T6 | `PATCH` / `DELETE` an existing event | 403 | as T5 |
| T7 | `GET /users/{coordinator}/mailboxSettings/workingHours` | 403 unless `MailboxSettings.Read` granted — note `getSchedule` returns working hours anyway | Decide whether the extra scope is needed (it is not) |
| T8 | Ask one coordinator to raise a colleague's sharing to *Limited details*, re-run T1 | `subject` now appears **for that person only** | This is the expected result. It proves sharing level is the control, and that it is per-person |
| T9 | Decode a token at [jwt.ms](https://jwt.ms) and read the `scp` / `roles` claims | Only `Calendars.Read.Shared`. **No `roles` claim at all** | A `roles` claim means app-only permissions are live |
| T10 | Inspect the app registration's "API permissions" blade | No application permissions, no admin consent granted | Revoke |

**T8 is the most important test and the one most likely to be skipped.** It is
the honest demonstration that the protection is real but *conditional*: it holds
because sharing is set to availability-only, and it would stop holding if
someone raised it. That is a finding to state to the group, not to hide — it
converts "the tool is safe" into "the tool is safe **and here is the setting
that keeps it safe**," which is the claim that survives contact with an auditor.

**Independent confirmation.** T1, T2 and T5 should be re-run by IT (or Robert),
from their own account, and the result attached to the ticket. Our own testing
is necessary but is not independent verification.

---

## 6. What to tell the group

Five sentences, in this order:

1. Graph **can** read event titles — the concern was correct, and the permission
   description that prompted it was accurate.
2. Whether it *does* is controlled by Exchange calendar **sharing level**, not by
   the app: at the tenant default, `subject` is never sent.
3. So under delegated sign-in the tool sees **exactly what you see in Outlook
   today** — busy, free, tentative, out of office — and nothing more, because the
   field is absent from the response, not merely ignored by our code.
4. The app-only alternative **cannot** be restricted to free/busy; Microsoft's
   least calendar app role reads subject and body. We are not proposing it.
5. The tool requests **no write permission**, so it cannot modify anyone's
   calendar; that is verified by test T5.

Then note the near-miss: the digit mapping we were given was inverted, and
building on it would have scheduled visits into busy slots.

---

## 7. Residual risks

| Risk | Severity | Mitigation |
|---|---|---|
| A coordinator raises sharing to Limited details; titles start arriving | Medium | **Strip `subject`, `location`, `isPrivate` at ingestion** and never persist them, regardless of what arrives. Belt and braces: the client must not store what it must not see. |
| Personal calendars connected into Outlook surface as busy blocks | Low, but was raised in the meeting | Only free/busy is read, so a personal event appears as an anonymous busy block. No content exposure. Worth saying out loud, since it was a specific worry. |
| Delegated token needs a human sign-in; blocks unattended runs | Medium | Scheduler signs in once a day, or use a dedicated service account with delegated consent. Decide before automating. Question 7 in the ticket. |
| Scope creep to `Calendars.Read` "just to make something work" | **High** | Pin the scope in config, assert it at startup, fail closed. See §8. |
| Someone consents application permissions later | High | T9/T10 as a recurring quarterly check, not a one-off |

---

## 8. Engineering changes — implemented

Built and tested on 18 August 2026. 15 privacy tests, 40 in the suite overall.
These encode the promises above so they cannot quietly lapse in a later refactor.

**`calendarsync.py`**
- Add `ALLOWED_SCOPES = frozenset({"Calendars.Read.Shared"})`. At startup, decode
  the token's `scp` claim and **refuse to run** if anything else is present or if
  a `roles` claim exists. Fail closed, loudly.
- In `GraphProvider.fetch`, drop `subject`, `location` and `isPrivate` from every
  `scheduleItem` before constructing `BusyBlock`. They must never reach the
  audit log, which is where PHI rules bite.
- Parse and store `workingHours` per coordinator.
- Correct any `availabilityView` handling to `0=free, 1=tentative, 2=busy,
  3=oof`, and prefer `scheduleItems[].status` where `workingElsewhere` matters.

**`feasibility.py`**
- Free requires `digit == 0` **and** inside `workingHours`. An empty calendar
  outside working hours is not availability.
- Keep `oof` a hard block; `tentative` and `workingElsewhere` stay soft flags.

**`models.py`** — add `working_hours` to `CalendarSnapshot`.

**`config.py`** — `graph_auth_mode: str = "delegated"` with a validator that
rejects `"application"` unless `allow_app_only_ack` is explicitly set, so the
riskier mode cannot be reached by accident.

**Tests** (`tests/test_graph_privacy.py`, 15 passing) — the documented
subject-bearing payload is ingested with subject, location and isPrivate
discarded and absent from the snapshot's repr; `fetch` raises on an app-only or
over-broad token rather than degrading to `sync_ok=False`; the digit mapping is
pinned against Copilot's inverted version; an empty calendar at 19:00 yields no
slot while 10:00 does; Outlook working hours override the local pattern for a
part-time coordinator.

**`graphcheck.py` + `make verify-graph`** — the ten-test protocol in §5 as a
runnable command. Probes T1 (no subjects returned), T1b (working hours present),
T2/T3/T4 (direct event reads refused), T9a/T9b (token claims). T5, the write
probe, is **skipped by default** because it creates a real event if the
guarantee is broken; it needs `--allow-write-probe` and an explicit warning is
printed. Output is written to `reports/graph-verification.txt` for attaching to
the IT ticket.

---

## 9. Open questions

1. Tenant default sharing level — unknown until IT confirms. Everything in §3
   rests on it being `AvailabilityOnly`.
2. Are there pre-existing elevated sharing grants among lab staff?
3. Unattended runs: service account with delegated consent, or accept a daily
   human sign-in?
4. Does the tenant permit an internal app registration without a security review?
5. Do UofSC IRB or IT policy treat coordinator free/busy as directory data or as
   personal data? Affects the retention window on the audit log.

---

## Sources

- [calendar: getSchedule](https://learn.microsoft.com/en-us/graph/api/calendar-getschedule)
- [scheduleInformation resource type](https://learn.microsoft.com/en-us/graph/api/resources/scheduleinformation)
- [scheduleItem resource type](https://learn.microsoft.com/en-us/graph/api/resources/scheduleitem)
- [Microsoft Graph permissions reference](https://learn.microsoft.com/en-us/graph/permissions-reference)
- [Role Based Access Control for Applications in Exchange Online](https://learn.microsoft.com/en-us/exchange/permissions-exo/application-rbac)
- [Application Access Policies (legacy)](https://learn.microsoft.com/en-us/exchange/permissions-exo/application-access-policies)
- [Get shared or delegated Outlook calendar and its events](https://learn.microsoft.com/en-us/graph/outlook-get-shared-events-calendars)
- [Allow Exchange Online Users to View Calendar Information](https://office365itpros.com/2021/09/21/exchange-view-calendar-information/)
- [Set default calendar permissions for all users with PowerShell](https://www.alitajran.com/set-default-calendar-permissions-for-all-users-powershell/)
- [Managing Calendar Permissions on Exchange Server and Microsoft 365](https://woshub.com/manage-calendar-permissions-exchange-microsoft-365/)
- [How to Configure RBAC for Applications in Exchange Online](https://www.alitajran.com/rbac-applications-exchange-online/)
