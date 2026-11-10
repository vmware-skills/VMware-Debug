"""Tools Cases callbacks using the existing facade runtime."""

from typing import Optional, Union


def register_callbacks(server, runtime, _case_error):
    @server.tool(name="build_incident_timeline", annotations=runtime._READ)
    @runtime.vmware_tool(risk_level="low", sensitive_params=["events"], sensitive_result=True)
    def build_incident_timeline(
        events: list[dict],
        bin_seconds: Optional[float] = None,
        z_threshold: float = 2.0,
        top_n: int = 5,
    ) -> dict:
        """[READ] Correlate already-fetched VMware events into one incident view.

        WHEN: use this after you've pulled events for an incident from the
        data-source skills (vmware-monitor get_events/get_alarms, vmware-aria
        list_alerts/list_anomalies, vmware-log-insight search_logs/aggregate_logs,
        vmware-nsx) — feed them here to find what correlates and where to look
        next. Not sure which events to pull? Run list_symptom_categories
        first. This tool does NOT fetch anything itself.

        RETURNS: {event_count, window, binning, classification, spikes
        (strongest anomalous bins), spikes_total, hypotheses (ranked
        root-cause candidates, each with a suggested_check), next_checks (which
        skill/tool to run next)}. Read `binning` for the resolution you were
        given, and `classification` for how much of the stream matched nothing —
        the ranking describes only the part that did.

        GOTCHAS: read-only, stateless, no network — nothing is executed.
        Remediation routes to vmware-aiops (single fix) or vmware-pilot
        (multi-step). A malformed event returns {error, hint} naming the
        offending index.

        Args:
            events: Event envelopes, each {ts, source, severity, entity, text,
                fields}. ts may be ISO-8601, epoch seconds or epoch millis and
                is required; severity is normalised onto critical/error/warning/
                info/unknown, so vendor spellings (fatal, red, warn, yellow,
                notice, green) are accepted. An entry that cannot be normalised
                is refused with its index, not skipped. Keep each event's
                event_type in fields — vmware-monitor's get_events returns it,
                and the symptom classifier matches it alongside the message. On
                a modern EventEx the message is generic boilerplate and the
                eventTypeId is the only thing that says what happened, so
                dropping it turns a readable event into an uncategorized one.
            bin_seconds: Time-bin width in seconds. Omit and it is chosen from
                event density off the ladder 1/10/60/300/900/3600/21600/86400,
                taking the finest width still averaging 4 events per bin.
            z_threshold: Standard deviations above the mean bin count that mark
                a spike (default 2.0). Under 3 bins, or a flat series, yields
                none at any threshold — empty spikes is not "calm".
            top_n: How many ranked hypotheses come back (default 5). Spikes are
                capped separately at 20, true count in 'spikes_total'.
        """
        try:
            return runtime.t.build_incident_timeline(events, bin_seconds, z_threshold, top_n)
        except Exception as exc:
            # Returned rather than raised, matching the rest of the family: the
            # caller gets a payload it can act on instead of a protocol fault,
            # and `hint` carries the recovery step the bare exception lacks.
            # No `items` key — a failed call must never read as an empty page.
            return {
                "error": runtime._safe_error(exc, "build_incident_timeline"),
                "hint": runtime._TIMELINE_ERROR_HINT,
            }

    @server.tool(name="list_symptom_categories", annotations=runtime._READ)
    @runtime.vmware_tool(risk_level="low")
    def list_symptom_categories() -> dict:
        """[READ] List the symptom categories vmware-debug recognises, each with
        example keywords and a suggested next check (which skill/tool to run).
        Takes no parameters. Use this when you don't yet know what to look
        at — it turns "something's wrong" into concrete investigation steps.
        Then gather the events those checks name and pass them to
        build_incident_timeline. Returns the family list envelope {items, returned,
        limit, total, truncated, hint}; each item is {category,
        example_keywords, suggested_check}. The routing table is a fixed
        constant, so truncated is always false and total exact —
        this is every category, not a page. Read-only; no network access."""
        return runtime.t.list_symptom_categories()

    @server.tool(name="open_case", annotations=runtime._WRITE_LOCAL)
    @runtime.vmware_tool(risk_level="low")
    def open_case(
        summary: str,
        determined_by: str,
        objects: Optional[list[str]] = None,
        window_start: Optional[str] = None,
        window_end: Optional[str] = None,
        product_versions: Optional[dict] = None,
    ) -> dict:
        """[WRITE] Open an investigation case — step 01, define the event.

        WHEN: at the start of an incident you expect to reason about rather than
        glance at. For a one-off lookup use build_incident_timeline alone.

        RETURNS: {case_id, path, state, grade, ceiling, ceiling_reasons, next}.
        Read `ceiling` now — it is the best grade this install can reach at all.

        GOTCHAS: writes only under $OPS_HOME, never to a VMware system, and
        never over an existing case.

        Args:
            summary: What is wrong, one line. plan_case reads it to infer the
                symptom category, so name the symptom in the estate's own words
                ("datastore latency on cluster-a"), not a diagnosis.
            determined_by: HOW the scope was decided — "user report", "vCenter
                alarm 42". Required: a scope from a phone call and one from an
                alarm id support different conclusions, and nobody remembers
                which it was later.
            objects: Entities in scope, names or MoIDs ("vm-web01", "host-12").
                Omit for a case not yet pinned to specific objects.
            window_start: When the incident is believed to have started,
                ISO-8601. Omit if unknown — evidence gets fetched against it,
                so an invented window is worse than none.
            window_end: Believed end, ISO-8601. Omit while still in progress.
            product_versions: Versions keyed by product — {"esxi": "8.0.3"} —
                plus "driver.<name>" / "firmware.<name>" entries. Knowledge-base
                entries are version-checked against this: one constraining a
                product absent here can support a hypothesis but never make the
                case Confirmed, since an unanswerable constraint is not a pass.
        """
        try:
            return runtime.t.open_case(
                summary=summary,
                determined_by=determined_by,
                objects=objects,
                window_start=window_start,
                window_end=window_end,
                product_versions=product_versions,
            )
        except Exception as exc:
            return _case_error(exc, "open_case")

    @server.tool(name="list_cases", annotations=runtime._READ)
    @runtime.vmware_tool(risk_level="low")
    def list_cases(limit: int = 50) -> dict:
        """[READ] List investigation cases, newest first.

        WHEN: to find the id of a case you or someone else opened earlier.
        Returns the family list envelope {items, returned, limit, total,
        truncated, hint}; each item is {case_id, summary, state, grade,
        opened_at}. A case whose folder is damaged appears with
        state="unreadable" rather than vanishing from the list.

        Args:
            limit: Maximum cases in the page, newest first (default 50). There
                is no offset — 'total' reports every case, so when truncated is
                true the only way to reach older ones is a larger limit.
        """
        try:
            return runtime.t.list_cases(limit=limit)
        except Exception as exc:
            return _case_error(exc, "list_cases")

    @server.tool(name="get_case", annotations=runtime._READ)
    @runtime.vmware_tool(risk_level="low")
    def get_case(case_id: str) -> dict:
        """[READ] One case: its scope, its ledger sizes, and its grade history.

        WHEN: to pick up an investigation, or to see why a case sits at the
        grade it does. Returns counts and identifiers rather than the whole
        ledger — read the case folder itself (the `path` from open_case) for
        full evidence bodies.

        RETURNS: {case_id, path, state, grade, opened_at, scope, evidence_count,
        sources, gap_count, blocking_gaps, grade_history}. `sources` is the
        distinct skills evidence came from, which is what corroboration is
        counted in.

        Args:
            case_id: The case id returned by open_case, or listed by list_cases.
                An unknown id is an error, never an empty case.
        """
        try:
            return runtime.t.get_case(case_id=case_id)
        except Exception as exc:
            return _case_error(exc, "get_case")

    @server.tool(name="submit_case_evidence", annotations=runtime._WRITE_LOCAL)
    @runtime.vmware_tool(risk_level="low", sensitive_params=["payload"])
    def submit_case_evidence(
        case_id: str,
        source_skill: str,
        source_tool: str,
        summary: str,
        query: Optional[dict] = None,
        fetched_at: Optional[str] = None,
        window_start: Optional[str] = None,
        window_end: Optional[str] = None,
        time_source: Optional[str] = None,
        clock_skew_s: Optional[float] = None,
        falsifies: Optional[list[str]] = None,
        knowledge_entry_id: Optional[str] = None,
        # Union, not dict: the documented payload shape includes a bare array of
        # events, and typed Optional[dict] that shape was refused by schema
        # validation before any of this skill's code ran.
        payload: Optional[Union[dict, list]] = None,
    ) -> dict:
        """[WRITE] Record one retrieved fact — steps 02/03 of the evidence loop.

        WHEN: after every read-tool call you intend to reason from.

        RETURNS: {case_id, evidence_id, payload_events, payload_note, grade,
        reasons} — the resulting grade, so you need no second call to see
        whether this changed anything, and what the payload was read as, so a
        summary submitted in place of a result is visible here rather than as a
        zero from record_case_timeline later.

        GOTCHAS: a fetch that failed or came back empty goes to record_case_gap,
        not here.

        Args:
            case_id: The case this fact belongs to (from open_case/list_cases).
                Required.
            source_skill: The skill that produced it, as the family spells it —
                "vmware-monitor", "vmware-aria", "vmware-log-insight". Required,
                non-blank. Two items from the SAME skill count as ONE source
                when corroboration is counted, so this string decides whether
                the case can reach Probable. Two reserved values name the
                knowledge layer instead: "knowledge-kb" and "knowledge-sr", the
                only sources that can be decisive.
            source_tool: The tool within that skill, e.g. "get_events".
                Required, non-blank — "monitor said so" is not reproducible.
            summary: What this item shows, one line.
            query: The exact parameters the tool was called with, so it can be
                re-run. Omit only if there genuinely were none.
            fetched_at: When the fetch happened, ISO-8601. Omit to stamp now —
                wrong for an item transcribed from earlier.
            window_start: Start of the period the DATA COVERS, ISO-8601 — not
                when it was fetched. get_events(hours=24) run at 10:00 and at
                18:00 answer different questions.
            window_end: End of the period the data covers, ISO-8601.
            time_source: Whose clock stamped it — "vcenter", "host" or "client".
                Recorded so a reader can judge whether two sources' timestamps
                compare; nothing in this release corrects for it. Null when
                unknown, never a guess.
            clock_skew_s: Known offset of that clock from UTC, in seconds.
                Recorded, likewise not applied. Null when unknown.
            falsifies: Hypothesis ids (H1, H2, …) this observation RULES OUT —
                the only route to Excluded; "we looked and found nothing" is a
                gap, not an exclusion. Ids must already exist via
                record_case_hypothesis; an unregistered id is refused and nothing is
                written.
            knowledge_entry_id: Which mounted knowledge entry this item IS.
                REQUIRED when source_skill is knowledge-kb or knowledge-sr —
                without it the entry's applies_to cannot be checked, so it
                counts as ordinary support and can never make the case
                Confirmed. get_case_knowledge lists what is mounted.
            payload: The read tool's RAW result, not a summary. For its events
                to reach record_case_timeline it must be a list of event dicts, or
                carry them under 'items', 'events' or 'rows'. What was found
                comes back as payload_events/payload_note.
        """
        try:
            return runtime.t.submit_case_evidence(
                case_id=case_id,
                source_skill=source_skill,
                source_tool=source_tool,
                query=query or {},
                summary=summary,
                fetched_at=fetched_at,
                window_start=window_start,
                window_end=window_end,
                time_source=time_source,
                clock_skew_s=clock_skew_s,
                falsifies=falsifies,
                knowledge_entry_id=knowledge_entry_id,
                payload=payload,
            )
        except Exception as exc:
            return _case_error(exc, "submit_case_evidence")

    @server.tool(name="record_case_gap", annotations=runtime._WRITE_LOCAL)
    @runtime.vmware_tool(risk_level="low")
    def record_case_gap(
        case_id: str,
        what: str,
        why: str,
        how_to_close: str,
        blocks: Optional[list[str]] = None,
        could_falsify: bool = False,
    ) -> dict:
        """[WRITE] Record something the investigation could NOT obtain.

        WHEN: any time a fetch failed, was refused, returned nothing, or the
        data simply does not exist in this environment. This is the tool that
        keeps a case honest: an unrecorded gap makes it look better supported
        than it is.

        RETURNS: {case_id, gap_id, grade, reasons}.

        GOTCHAS: recording a gap does not punish the case for the evidence it
        does have — a missing confirmation caps the grade, it does not demote
        it. Writing gaps down is meant to be free.

        Args:
            case_id: The case this gap belongs to (from open_case/list_cases).
            what: The observation that could not be obtained — the thing you
                wanted, not the error you got.
            why: Why it could not be had — refused, unreachable, collected by
                nothing in this family, absent from this environment.
            how_to_close: The next action that would close it, even outside this
                system ("open a vendor SR"). A gap with no stated next action
                reads like a to-do and gets skipped.
            blocks: Hypothesis ids (H1, H2, …) this gap holds up; they must
                already exist via record_case_hypothesis. Empty is fine.
            could_falsify: Would OBTAINING this be able to prove the hypothesis
                WRONG? false (default) is the ordinary case — missing
                corroboration, capping the case below Confirmed. true means it
                could overturn the hypothesis, holding it at Candidate.
        """
        try:
            return runtime.t.record_case_gap(
                case_id=case_id,
                what=what,
                why=why,
                how_to_close=how_to_close,
                blocks=blocks,
                could_falsify=could_falsify,
            )
        except Exception as exc:
            return _case_error(exc, "record_case_gap")

    return (
        build_incident_timeline,
        list_symptom_categories,
        open_case,
        list_cases,
        get_case,
        submit_case_evidence,
        record_case_gap,
    )
