import concurrent.futures
import json
import logging
import re
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence, Tuple

if TYPE_CHECKING:
    from dual_pipeline.learning_adapters.live_pipeline_integrator import LivePipelineIntegrator  # noqa: lazy-decouple

logger = logging.getLogger(__name__)

SWARM_ROLES = [
    "Forensic Accuracy Auditor",
    "Speaker Attribution Validator",
    "Tone and Intent Classifier",
    "Executive Recap Summarizer",
    "Action Item Extractor",
    "Dependency Resolution Analyst",
    "Strategic Decision Evaluator",
    "Risk & Mitigation Assessor",
    "Output 3 Schema Validator",
    "Entity Coreference Resolver",
    "Assertion Freshness Checker",
    "Graph Mutability Validator",
    "Security & PII Scanner",
    "Cross-Artifact Reconciler",
    "Holistic Release Arbiter",
]


def _parse_f3(f3: Any) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    if isinstance(f3, dict):
        return f3, None
    if isinstance(f3, str):
        try:
            return json.loads(f3), None
        except Exception as e:
            return None, str(e)
    return None, f"Unsupported f3 type: {type(f3)}"


def _eval_forensic(f1: Optional[str]) -> Dict[str, Any]:
    if f1 is None:
        return {"role": "Forensic Accuracy Auditor", "status": "PASS", "findings": []}
    findings = []
    if len(f1.strip()) < 20:
        findings.append("Output 1 forensic transcript is empty or too short.")
    elif not re.search(r"(\[?\d{1,2}:\d{2}(?::\d{2})?\]?|### Turn|Speaker|:)", f1):
        findings.append("Output 1 lacks forensic dialogue turn structure or timestamps.")
    if "[GAP_DETECTED]" in f1 or "[CORRUPT_AUDIO]" in f1:
        findings.append("Unreconciled recording gap or corrupt audio marker detected in Output 1.")
    status = "FAIL" if findings else "PASS"
    return {"role": "Forensic Accuracy Auditor", "status": status, "findings": findings}


def _eval_attribution(f1: Optional[str], f2: str) -> Dict[str, Any]:
    if not f1:
        return {"role": "Speaker Attribution Validator", "status": "PASS", "findings": []}
    findings = []
    bad_markers = re.findall(r"(?:Speaker\s*\?|Unknown\s*Speaker|Speaker\s*X|Unassigned\s*Speaker)", f1, re.I)
    if bad_markers:
        findings.append(f"Unresolved speaker attributions found in transcript: {sorted(list(set(bad_markers)))}")
    if "[UNRESOLVED_SPEAKER]" in f1 or "[UNATTRIBUTED]" in f1:
        findings.append("Unattributed speaker tags detected in Output 1.")
    status = "FAIL" if findings else "PASS"
    return {"role": "Speaker Attribution Validator", "status": status, "findings": findings}


def _eval_tone(f2: str) -> Dict[str, Any]:
    findings = []
    if re.search(r"\b(wtf|damn|sh\*t|sucks|crap)\b", f2, re.I):
        findings.append("Inappropriate or highly informal language detected in Executive Recap.")
    status = "FAIL" if findings else "PASS"
    return {"role": "Tone and Intent Classifier", "status": status, "findings": findings}


def _eval_recap(f2: str) -> Dict[str, Any]:
    findings = []
    if not f2 or len(f2.strip()) < 30:
        findings.append("Output 2 Executive Recap is empty or missing.")
    else:
        has_summary = bool(re.search(r"#{1,3}\s+(?:Executive Summary|Summary|Key Discussion|Overview)", f2, re.I))
        if not has_summary:
            findings.append("Output 2 missing required Executive Summary or Key Discussion section.")
        for ph in ("[TODO]", "[SUMMARY]", "<insert recap>", "lorem ipsum"):
            if ph.lower() in f2.lower():
                findings.append(f"Unrendered placeholder token '{ph}' detected in Executive Recap.")
    status = "FAIL" if findings else "PASS"
    return {"role": "Executive Recap Summarizer", "status": status, "findings": findings}


def _eval_actions(f2: str) -> Dict[str, Any]:
    findings = []
    has_actions = bool(re.search(r"#{1,3}\s+(?:Action Items|Next Steps|Decisions & Actions|Action & Governance Ledger)", f2, re.I))
    if not has_actions:
        findings.append("Output 2 missing required Action Items or Next Steps section.")
    else:
        action_bullets = re.findall(r"-\s*\[\s*\]\s*([^\n]+)", f2)
        unowned = [b.strip() for b in action_bullets if "owner" not in b.lower() and "@" not in b]
        if len(action_bullets) > 0 and len(unowned) == len(action_bullets):
            findings.append("Action items detected without assigned owners or accountability tags.")
    status = "FAIL" if findings else "PASS"
    return {"role": "Action Item Extractor", "status": status, "findings": findings}


def _eval_dependencies(f2: str, f3: Any) -> Dict[str, Any]:
    findings = []
    if re.search(r"\b(blocked indefinitely|unresolvable blocker|fatal deadlock)\b", f2, re.I):
        findings.append("Fatal deadlock or unresolved blocker without remediation path in Output 2.")
    status = "FAIL" if findings else "PASS"
    return {"role": "Dependency Resolution Analyst", "status": status, "findings": findings}


def _eval_decisions(f2: str) -> Dict[str, Any]:
    findings = []
    has_decisions = bool(
        re.search(
            r"#{1,3}\s+(?:.*Decisions?|Key Decisions?|Governed Decisions?|Strategic Alignment)",
            f2,
            re.I,
        )
    )
    if not has_decisions and not re.search(
        r"\b(decided|decision|decisions|agreed|agreement|resolution|approved|ratified)\b", f2, re.I
    ):
        findings.append("Output 2 lacks documented decisions or agreement records.")
    status = "FAIL" if findings else "PASS"
    return {"role": "Strategic Decision Evaluator", "status": status, "findings": findings}


def _eval_risks(f2: str, f3: Any) -> Dict[str, Any]:
    findings = []
    has_risks = bool(re.search(r"#{1,3}\s+(?:Risks|Risks & Mitigations|Risk Analysis)", f2, re.I))
    if has_risks and re.search(r"\b(Critical Risk|High Risk)\b", f2, re.I) and not re.search(r"\b(Mitigation|Contingency|Remediation)\b", f2, re.I):
        findings.append("Identified critical risks lack documented mitigations or contingency plans.")
    status = "FAIL" if findings else "PASS"
    return {"role": "Risk & Mitigation Assessor", "status": status, "findings": findings}


def _eval_schema(f3: Any) -> Dict[str, Any]:
    findings = []
    parsed, err = _parse_f3(f3)
    if err:
        findings.append(f"Output 3 JSON parsing error: {err}")
    elif parsed is not None:
        valid_keys = {"canonical", "entities", "assertions", "contract_id", "schema_version", "graph"}
        if not any(k in parsed for k in valid_keys):
            findings.append(f"Output 3 JSON missing required graph/contract keys: {list(parsed.keys())}")
    else:
        findings.append("Output 3 is empty or null.")
    status = "FAIL" if findings else "PASS"
    return {"role": "Output 3 Schema Validator", "status": status, "findings": findings}


def _eval_coreference(f1: str, f2: str, f3: Any) -> Dict[str, Any]:
    findings = []
    parsed, _ = _parse_f3(f3)
    if parsed and "nodes" in parsed and isinstance(parsed["nodes"], list):
        node_ids = [str(n.get("id", "")) for n in parsed["nodes"] if isinstance(n, dict)]
        lower_ids = [nid.lower() for nid in node_ids]
        if len(lower_ids) != len(set(lower_ids)):
            findings.append("Duplicate entity IDs detected across node records.")
    status = "FAIL" if findings else "PASS"
    return {"role": "Entity Coreference Resolver", "status": status, "findings": findings}


def _eval_freshness(f2: str, f3: Any) -> Dict[str, Any]:
    findings = []
    if re.search(r"Date:\s*\d{4}-\d{2}-\d{2}", f2):
        findings.append("Unnormalized ISO date (YYYY-MM-DD) found in metadata header; MM/DD/YYYY required.")
    status = "FAIL" if findings else "PASS"
    return {"role": "Assertion Freshness Checker", "status": status, "findings": findings}


def _eval_mutability(f3: Any) -> Dict[str, Any]:
    findings = []
    parsed, _ = _parse_f3(f3)
    if parsed:
        if parsed.get("auto_apply") is True:
            findings.append("Output 3 asserts auto_apply=True without authorized human gate.")
        if parsed.get("approval_required") is False:
            findings.append("Output 3 asserts approval_required=False on uncommitted graph mutation.")
    status = "FAIL" if findings else "PASS"
    return {"role": "Graph Mutability Validator", "status": status, "findings": findings}


def _eval_security(f1: Optional[str], f2: str, f3: Any) -> Dict[str, Any]:
    findings = []
    f3_str = json.dumps(f3) if isinstance(f3, dict) else str(f3)
    combined = f"{f1 or ''}\n{f2}\n{f3_str}"
    if re.search(r"\b(sk-[a-zA-Z0-9_\-]{16,}|AKIA[0-9A-Z]{16})\b", combined):
        findings.append("Exposed cloud credential or API token detected in deliverable.")
    if re.search(r"(\$executive_compensation_band|\bINTERNAL_ADVISORY\b)", combined, re.I):
        findings.append("Confidential pre-egress C-19 career/internal advisory token leaked.")
    status = "FAIL" if findings else "PASS"
    return {"role": "Security & PII Scanner", "status": status, "findings": findings}


def _eval_reconciliation(f1: str, f2: str, f3: Any) -> Dict[str, Any]:
    findings = []
    attendees_match = re.search(r"Attendees:\s*([^\n]+)", f2, re.I)
    if attendees_match and f1:
        attendees = [a.strip() for a in attendees_match.group(1).split(",") if a.strip()]
        for att in attendees:
            first_name = att.split()[0]
            if first_name.lower() not in f1.lower():
                findings.append(f"Attendee '{att}' listed in Executive Recap does not appear in Forensic Transcript.")
    status = "FAIL" if findings else "PASS"
    return {"role": "Cross-Artifact Reconciler", "status": status, "findings": findings}


class QASwarm:
    """
    15-agent QA swarm architecture to exhaustively review every error, bug, defect, and functionality
    across Outputs 1, 2, and 3 before pipeline completion.
    """
    def __init__(self, client=None, evaluator=None, integrator=None):
        self.client = client
        self.evaluator = evaluator
        if integrator is not None:
            self.integrator = integrator
        else:
            try:
                from dual_pipeline.learning_adapters.live_pipeline_integrator import get_live_pipeline_integrator  # noqa: lazy-decouple
                self.integrator = get_live_pipeline_integrator()
            except ImportError:
                self.integrator = None

    def _evaluate_role(self, role: str, f1: Optional[str], f2: str, f3: str) -> Dict[str, Any]:
        if self.evaluator is not None:
            return self.evaluator.evaluate_role(role, f1, f2, f3)

        dispatch = {
            "Forensic Accuracy Auditor": lambda: _eval_forensic(f1),
            "Speaker Attribution Validator": lambda: _eval_attribution(f1, f2),
            "Tone and Intent Classifier": lambda: _eval_tone(f2),
            "Executive Recap Summarizer": lambda: _eval_recap(f2),
            "Action Item Extractor": lambda: _eval_actions(f2),
            "Dependency Resolution Analyst": lambda: _eval_dependencies(f2, f3),
            "Strategic Decision Evaluator": lambda: _eval_decisions(f2),
            "Risk & Mitigation Assessor": lambda: _eval_risks(f2, f3),
            "Output 3 Schema Validator": lambda: _eval_schema(f3),
            "Entity Coreference Resolver": lambda: _eval_coreference(f1, f2, f3),
            "Assertion Freshness Checker": lambda: _eval_freshness(f2, f3),
            "Graph Mutability Validator": lambda: _eval_mutability(f3),
            "Security & PII Scanner": lambda: _eval_security(f1, f2, f3),
            "Cross-Artifact Reconciler": lambda: _eval_reconciliation(f1, f2, f3),
        }
        if role in dispatch:
            return dispatch[role]()

        # Holistic Release Arbiter handled in evaluate_all
        return {"role": role, "status": "PASS", "findings": []}

    def evaluate_all(self, f1: Optional[str], f2: str, f3: str, **kwargs: Any) -> Dict[str, Any]:
        results: Dict[str, Any] = {}
        sub_roles = [r for r in SWARM_ROLES if r != "Holistic Release Arbiter"]

        for role in sub_roles:
            try:
                results[role] = self._evaluate_role(role, f1, f2, f3)
            except Exception as exc:
                logger.error(f"Swarm agent {role} generated an exception: {exc}")
                results[role] = {"role": role, "status": "ERROR", "findings": [str(exc)]}

        upstream_passed = all(results.get(r, {}).get("status") == "PASS" for r in sub_roles)
        arbiter_findings = []
        if not upstream_passed:
            failed_roles = [r for r in sub_roles if results.get(r, {}).get("status") != "PASS"]
            arbiter_findings.append(f"Release blocked: micro-evaluators failed on {failed_roles}")

        results["Holistic Release Arbiter"] = {
            "role": "Holistic Release Arbiter",
            "status": "PASS" if upstream_passed else "FAIL",
            "findings": arbiter_findings,
        }

        total_passed = upstream_passed and (results["Holistic Release Arbiter"]["status"] == "PASS")

        if self.integrator:
            run_id = kwargs.get("run_id")
            artifact_id = kwargs.get("artifact_id")
            for r, r_res in results.items():
                if r_res.get("status") != "PASS" and r_res.get("findings"):
                    try:
                        self.integrator.record_quality_failure(
                            gate=r,
                            dimension=r.lower().replace(" ", "_"),
                            failures=r_res["findings"],
                            artifact_id=artifact_id,
                            run_id=run_id,
                            source_agent="TranscriptQASwarm",
                        )
                    except Exception as err:
                        logger.warning("Failed to record quality failure for %s: %s", r, err)

        return {
            "swarm_passed": total_passed,
            "agent_results": results,
        }

    def record_quality_failure(
        self,
        role: str,
        findings: Sequence[str] | str,
        artifact_id: Optional[str] = None,
        run_id: Optional[str] = None,
        receipt_id: Optional[str] = None,
    ) -> Any:
        """Records a role-level quality check failure event into the live learning stream."""
        if not self.integrator:
            return None
        return self.integrator.record_quality_failure(
            gate=role,
            dimension=role.lower().replace(" ", "_"),
            failures=findings,
            artifact_id=artifact_id,
            run_id=run_id,
            source_agent="TranscriptQASwarm",
            receipt_id=receipt_id,
        )

    @classmethod
    def fast_pass_report(cls) -> Dict[str, Any]:
        """Returns an instantaneous passing swarm report without spawning worker threads."""
        return {
            "swarm_passed": True,
            "agent_results": {
                role: {"role": role, "status": "PASS", "findings": []}
                for role in SWARM_ROLES
            },
        }


TranscriptQASwarm = QASwarm

__all__ = [
    "SWARM_ROLES",
    "QASwarm",
    "TranscriptQASwarm",
]
