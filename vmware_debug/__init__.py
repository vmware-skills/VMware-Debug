"""vmware-debug — VMware diagnostic brain.

Offline incident triage: correlate events from monitor/aria/log-insight/nsx
into a unified timeline, detect spikes, rank root-cause hypotheses, and route
remediation to vmware-aiops / vmware-pilot. Never writes to any VMware system
and never executes fixes; its only writes go to the local case ledger.
"""

__version__ = "1.12.2"
