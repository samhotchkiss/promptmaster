---
name: azure-cost-optimization
description: Reduce Azure spend — right-sizing, reservations, idle resource cleanup, tag hygiene.
when_to_trigger:
  - azure cost
  - reduce cloud spend
  - cost optimization
  - azure bill
kind: magic_skill
attribution: https://github.com/Azure/azure-cli
---

# Azure Cost Optimization

## When to use

Use when the Azure bill is higher than expected, before a renewal review, or as a quarterly hygiene pass. Most Azure overspend comes from three places: oversized VMs, forgotten resources, and unused reserved capacity. This skill attacks all three.

## Process

1. **Start with Cost Management + Billing.** Filter by Subscription and group by Service. The 80/20 rule holds: two or three services are ~80% of the bill. Optimize those first.
2. **Tag everything.** `environment`, `owner`, `project`, `cost-center`. Any resource without tags is a resource you cannot attribute and thus cannot justify. Enforce via Azure Policy: untagged resources are non-compliant.
3. **Right-size VMs based on actual usage.** Azure Advisor's "Optimize virtual machine spend" shows 14-day CPU/memory and suggests smaller SKUs. Apply for anything under 40% average CPU — you are paying for capacity you do not use.
4. **Use reservations or savings plans for predictable workloads.** 3-year Reserved Instances for VMs you know will run 24/7 = ~62% discount. 1-year = ~40%. Saving plans are more flexible (apply across SKUs) at slightly lower discount.
5. **Delete orphaned resources.** Public IPs not attached to anything, unattached disks, old snapshots, stopped-but-not-deallocated VMs (still billed). `az resource list` + scripting, or use the Azure Resource Graph.
6. **Auto-shutdown dev VMs.** Dev/test VMs off overnight and weekends cut compute cost ~65%. Schedule via `Microsoft.DevTestLab/schedules` or Automation runbooks.
7. **Log Analytics retention tuning.** Default is 31 days; many teams pay for 90+. Cut to 31 and archive to cheap storage if you need audit history. Same goes for Application Insights.
8. **Monitor spend with budgets + alerts.** `az consumption budget create` tied to email + webhook at 80% and 100% of monthly cap. No budget = no feedback loop.

## Example invocation

```bash
# 1. Find top-spending services
az costmanagement query \
  --type ActualCost \
  --scope "/subscriptions/$SUB_ID" \
  --timeframe MonthToDate \
  --dataset-aggregation '{"totalCost": {"name": "PreTaxCost", "function": "Sum"}}' \
  --dataset-grouping '[{"type": "Dimension", "name": "ServiceName"}]'

# 2. Find untagged resources
az graph query -q "Resources | where isempty(tags) | project name, type, resourceGroup" --output table

# 3. Find unattached public IPs
az network public-ip list --query "[?ipConfiguration==null].{name:name, rg:resourceGroup, ip:ipAddress}" -o table

# 4. Find unattached managed disks
az disk list --query "[?diskState=='Unattached'].{name:name, rg:resourceGroup, gb:diskSizeGB}" -o table

# 5. Stopped-but-not-deallocated VMs (still billed for compute)
az vm list -d --query "[?powerState=='VM stopped' || powerState=='VM generalized'].{name:name, rg:resourceGroup, state:powerState}" -o table

# 6. Right-sizing recommendations
az advisor recommendation list --category Cost --query "[].{resource:resourceMetadata.resourceId, impact:impact, description:shortDescription.problem}"

# 7. Monthly budget alert
az consumption budget create \
  --budget-name monthly-prod \
  --amount 10000 \
  --time-grain Monthly \
  --start-date 2026-04-01 \
  --end-date 2027-04-01 \
  --notifications "actual_80=[{threshold:80,operator:GreaterThan,contactEmails:[team@example.com]}]"
```

## Outputs

- A ranked list of top-spending services with % of total.
- A count of untagged, orphaned, and over-provisioned resources.
- A cleanup script (dry-run first!) that deletes the orphans.
- A written recommendation for reservations/savings plans.
- Budget alerts wired to email.

## Common failure modes

- "Stopped" VMs without "deallocated"; still billed for compute hours.
- Buying reservations without usage data; locking in over-provisioned SKUs.
- Tag hygiene ignored; cannot chargeback to teams, nobody is accountable.
- Log Analytics retention set once and forgotten; long retention is the hidden tax.
