"""Tools Runs callbacks using the existing facade runtime."""

from typing import Optional


def register_callbacks(server, runtime, _case_error):
    @server.tool(name="grade_case", annotations=runtime._WRITE_LOCAL)
    @runtime.vmware_tool(risk_level="low")
    def grade_case(case_id: str) -> dict:
        """[WRITE] Compute and record the conclusion grade — steps 07/08.

        WHEN: when you think the investigation has reached a conclusion, or to
        record where it stands before handing it over.

        There is deliberately NO parameter for the grade. You cannot state a
        conclusion level; it is recomputed from the ledger on every call. If
        you disagree with the result, change the ledger — submit the evidence
        that is missing, or record the gap that is blocking it.

        The levels: Candidate (a hypothesis exists); Probable (at least two
        INDEPENDENT sources agree — two calls to the same skill are one source
        — and nothing outstanding could overturn it); Confirmed (that, plus a
        decisive item: a direct hardware diagnostic, a version-checked
        knowledge-base entry, or a vendor SR, and no gap left open); Excluded
        (an observation that actually rules the hypothesis out — "we looked and
        found nothing" is a gap, not an exclusion). Exclusion is per
        hypothesis, counting only sources that falsify THAT hypothesis; the
        case is Excluded only when every registered one is. A hypothesis
        contradicted but not yet ruled out holds the case at Candidate.
        Otherwise the grade counts only evidence that falsifies nothing.

        RETURNS: {grade, previous, direction, reasons, ceiling, ceiling_reasons,
        rules_source, rules_origin, hypotheses}. `hypotheses` lists every
        registered hypothesis as {id, status: open|excluded}. `direction` is
        initial/up/down/unchanged — grades may go DOWN, and the history records
        it when they do.

        GOTCHAS: on a stock install `ceiling` is "probable", because Confirmed
        needs a decisive source and there is neither a hardware-diagnostic
        channel nor a knowledge library mounted yet. That is a real limit, not
        a caution. Every grading is appended to conclusion.md and none is ever
        rewritten.

        Args:
            case_id: The case to grade (from open_case/list_cases). This is the
                only parameter — see above for why there is no grade parameter.
        """
        try:
            return runtime.t.grade_case(case_id=case_id)
        except Exception as exc:
            return _case_error(exc, "grade_case")

    @server.tool(name="get_case_readiness", annotations=runtime._READ)
    @runtime.vmware_tool(risk_level="low")
    def get_case_readiness(available_skills: Optional[list[str]] = None) -> dict:
        """[READ] What strength of conclusion can this environment reach?

        WHEN: before starting an investigation, or when a case will not go
        higher and you want to know whether that is fixable. Answering this
        first is worth far more than discovering it halfway through.

        RETURNS: {classes, categories, unrecognised_skills, note}. A name that
        matched no skill in the catalogue comes back in `unrecognised_skills`
        rather than being absorbed into "not installed" — otherwise a typo reads
        as advice to install something you already have. Per evidence class:
        whether it is
        available, through which tools, and if not, `how_to_supply`. Per
        symptom category (storage, network, compute, ha_drs, configuration,
        accelerator, kubernetes, hardware, host_lifecycle, power_lifecycle,
        auth, platform, licensing, data_collection): a `ceiling` and the
        `independent_sources` behind it. There is deliberately no single score —
        "readiness 78%" cannot be acted on, "storage reaches Probable, hardware
        reaches Candidate" can.

        GOTCHAS: two classes served by the SAME skill count as one source, so
        two available classes do not always mean Probable. The hardware class is
        unavailable no matter what is installed — nothing in this family reaches
        below ESXi — and the knowledge class becomes available only when entries
        are mounted under $OPS_HOME/knowledge/.

        Args:
            available_skills: The skills actually installed and configured, in
                either of the family's spellings — "monitor" and "vmware-monitor"
                name the same thing. Omit to assume all of them, which reports
                the ceiling imposed by the family itself rather than by this
                install. A name matching no catalogued skill comes back in
                `unrecognised_skills` rather than being read as "not installed".
        """
        try:
            return runtime.t.get_case_readiness(available_skills=available_skills)
        except Exception as exc:
            return _case_error(exc, "get_case_readiness")

    @server.tool(name="plan_case", annotations=runtime._READ)
    @runtime.vmware_tool(risk_level="low")
    def plan_case(
        case_id: str,
        category: Optional[str] = None,
        available_skills: Optional[list[str]] = None,
        max_steps: int = runtime.DEFAULT_MAX_STEPS,
    ) -> dict:
        """[READ] What to fetch next for this case — step 02, recomputed each call.

        WHEN: right after open_case, and again after each round of evidence. It
        is not a checklist: submit something and the next plan is shorter, lose
        a source and it routes around it.

        RETURNS: {category, category_signals, steps, already_covered,
        held_back, unavailable,
        ceiling, note}. Steps are interleaved across evidence classes, so you
        get breadth before depth — corroboration is counted in distinct sources,
        which is what actually moves the grade.
        Each step is {evidence_class, skill, tool, purpose, objects, window,
        degraded} — call that skill's tool, then submit the result with
        submit_case_evidence.

        GOTCHAS: `unavailable` is the important half. A source this install
        cannot reach is listed there with how_to_supply rather than left out, so
        the gap is visible now instead of when the conclusion refuses to firm
        up. An empty `steps` is never silent — `note` says whether everything
        reachable is already in, or whether nothing here can be reached.
        `category_signals` names the words that chose the category, and any
        category that also matched — check it first, since the rest runs off
        that one word.

        Args:
            case_id: The case to plan for (from open_case/list_cases).
            category: Force the symptom class instead of inferring it. Exactly
                one of: storage, network, compute, ha_drs, configuration,
                accelerator, kubernetes, hardware, host_lifecycle,
                power_lifecycle, auth, platform, licensing,
                data_collection. Omit to infer, then read
                `category_signals` for the word that decided it.
            available_skills: Narrow to the skills actually installed, in either
                spelling ("monitor" or "vmware-monitor"). Omit to assume all of
                them, which can produce steps this install cannot run.
            max_steps: How many steps to return, at least 1. The default of 6
                is two rounds across three evidence classes — enough to reach
                the two independent sources a Probable grade costs, without
                handing a model a fourteen-item menu it will work through. Raise
                it when `held_back` says there is more for this category; a
                storage case has fourteen reachable tools. Below 1 is refused
                rather than obeyed, because an empty plan already means
                "nothing left to fetch".
        """
        try:
            return runtime.t.plan_case(
                case_id=case_id,
                category=category,
                available_skills=available_skills,
                max_steps=max_steps,
            )
        except Exception as exc:
            return _case_error(exc, "plan_case")

    @server.tool(name="record_case_hypothesis", annotations=runtime._WRITE_LOCAL)
    @runtime.vmware_tool(risk_level="low")
    def record_case_hypothesis(case_id: str, statement: Optional[str] = None) -> dict:
        """[WRITE] Register a candidate explanation — step 06. Called without
        `statement` it writes nothing and only reads the hypothesis ledger.

        WHEN: as soon as you have a theory worth testing, and again to see where
        each one stands. Pass `statement` to add one; omit it to just read.

        Every hypothesis gets an id (H1, H2, …). Those ids are what
        record_case_gap(blocks=[...]) and submit_case_evidence(falsifies=[...])
        refer to, and an id that was never registered is REFUSED rather than
        ignored — a dangling reference blocks nothing and falsifies nothing,
        which quietly reports a stronger case than you have.

        RETURNS: {case_id, added, hypotheses, note}. Each entry carries its
        status: `refuted` (ruled out by grade_case's rule), `blocked` (a gap is
        in the way, with the gap id and how to close it), or `open`.
        `refuted_by` lists every falsifying item; the first next step says when
        more sources are needed. Status is computed from what points at the
        hypothesis, never asserted.

        GOTCHAS: refuted outranks blocked. Once an observation settles the
        question, a missing measurement no longer matters.

        Args:
            case_id: The case whose hypothesis ledger this is (from
                open_case/list_cases).
            statement: The candidate explanation, in one line. Pass it to
                register a new hypothesis, which is assigned the next id (H1,
                H2, …); omit it to read the ledger without changing it. There is
                no parameter for a hypothesis's status — status is computed from
                the evidence and gaps that point at it.
        """
        try:
            return runtime.t.record_case_hypothesis(case_id=case_id, statement=statement)
        except Exception as exc:
            return _case_error(exc, "record_case_hypothesis")

    @server.tool(name="record_case_timeline", annotations=runtime._WRITE_LOCAL)
    @runtime.vmware_tool(risk_level="low", sensitive_result=True)
    def record_case_timeline(
        case_id: str,
        bin_seconds: Optional[float] = None,
        z_threshold: float = 2.0,
        top_n: int = 5,
    ) -> dict:
        """[WRITE] Correlate everything this case has collected — steps 04/05.

        WHEN: once evidence is in. Unlike build_incident_timeline, this takes no
        events: it reads the payloads already submitted, so the result is
        reproducible from the case folder alone months later, on a machine with
        access to nothing.

        RETURNS: {event_count, window, binning, classification, spikes,
        spikes_total, hypotheses, evidence_without_events,
        evidence_without_events_detail, rejected, note} and writes timeline.md.

        GOTCHAS: `note` distinguishes three states that all show zero events —
        no evidence submitted at all, evidence that carried none, and a genuinely
        quiet window — and names which items carried none, with what they held
        instead. `rejected` names any row that could not be read, with the
        evidence item it came from; dropping those silently would shrink the
        picture the conclusion rests on. Submit a read tool's raw result as
        `payload` for its events to reach here — a summary of the result carries
        no rows, and submit_case_evidence says so at the time.

        Args:
            case_id: The case whose submitted payloads are correlated (from
                open_case/list_cases). Unlike build_incident_timeline this takes no
                events — everything comes from the case folder.
            bin_seconds: Time-bin width in seconds. Omit and it is chosen from
                event density (ladder 1..86400, finest width still averaging 4
                events per bin); `binning` reports which was used.
            z_threshold: Standard deviations above the mean bin count that mark
                a spike (default 2.0). Under 3 bins, or a flat series, yields
                none at any threshold.
            top_n: How many ranked hypotheses come back (default 5). Spikes are
                capped separately at 20, true count in 'spikes_total'.
        """
        try:
            return runtime.t.record_case_timeline(
                case_id=case_id,
                bin_seconds=bin_seconds,
                z_threshold=z_threshold,
                top_n=top_n,
            )
        except Exception as exc:
            return _case_error(exc, "record_case_timeline")

    @server.tool(name="close_case", annotations=runtime._WRITE_LOCAL)
    @runtime.vmware_tool(risk_level="low")
    def close_case(case_id: str) -> dict:
        """[WRITE] Record the final grade and archive the case — step 08.

        WHEN: when the investigation is finished, or is being handed over.
        Closing turns a working folder into a record other people rely on, so it
        computes and records the grade rather than accepting one.

        RETURNS: {case_id, state, grade, open_gaps, path, note}. `open_gaps`
        names anything still blocking a hypothesis at the moment of closing —
        stated here rather than left in the file for someone to find.

        GOTCHAS: a closed case is not closed again and its record is never
        rewritten. To reopen the question, open a new case that cites this one,
        so the original conclusion and whatever changed it both stay readable.

        Args:
            case_id: The case to close (from open_case/list_cases). There is no
                grade parameter — closing recomputes the grade from the ledger.
                An already-closed case is refused rather than closed again.
        """
        try:
            return runtime.t.close_case(case_id=case_id)
        except Exception as exc:
            return _case_error(exc, "close_case")

    @server.tool(name="get_case_knowledge", annotations=runtime._READ)
    @runtime.vmware_tool(risk_level="low")
    def get_case_knowledge(case_id: Optional[str] = None) -> dict:
        """[READ] What the knowledge layer accepts, and what is mounted.

        WHEN: when someone asks what can be added to make conclusions stronger,
        or when a case will not reach Confirmed and you need to say why in terms
        they can act on. This is the answer to "which knowledge formats do you
        take" — the first question anyone mounting a library asks.

        RETURNS: {root, sections, entries, with_applies_to, by_source,
        unreadable, unsupported, formats, needs_conversion, note} — plus
        {applicable, decisive_here} when a case_id is given.

        `formats` lists every extension read and how each carries its metadata:
        Markdown front-matter, YAML/JSON whole-file, JSONL per line, CSV/TSV per
        row, and plain text with a sibling .yaml. `needs_conversion` names the
        ones that must become Markdown first (PDF, DOCX, PPTX, HTML).

        GOTCHAS: an entry is decisive ONLY if its `applies_to` block was checked
        against the case scope and passed — matching is by version
        applicability, never by similarity, because an entry written for the
        wrong build reads exactly like the right one. An entry with no
        `applies_to` can support a hypothesis but can never make a case
        Confirmed. A constraint the case scope cannot answer is not a match
        either: silence is not a pass.

        Args:
            case_id: Omit to describe the knowledge layer itself — what formats
                are read and what is mounted. Pass a case id and every mounted
                entry is additionally version-checked against THAT case's
                product_versions, adding `applicable` and `decisive_here` with
                the reason each entry did or did not qualify.
        """
        try:
            return runtime.t.get_case_knowledge(case_id=case_id)
        except Exception as exc:
            return _case_error(exc, "get_case_knowledge")

    return (
        grade_case,
        get_case_readiness,
        plan_case,
        record_case_hypothesis,
        record_case_timeline,
        close_case,
        get_case_knowledge,
    )
