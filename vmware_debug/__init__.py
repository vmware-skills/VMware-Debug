"""vmware-debug — VMware diagnostic brain.

Read-only incident triage: correlate events from monitor/aria/log-insight/nsx
into a unified timeline, detect spikes, rank root-cause hypotheses, and route
remediation to vmware-aiops / vmware-pilot. Never writes; never executes fixes.
"""

__version__ = "1.8.1"
