"""The corpus: four policy documents, authored for this project.

Every clause number here is a claim the demo makes on screen ("[SLA · §3.2]"),
so the numbers are fixed and the code that computes an amount reads the same
threshold. `tests/test_corpus.py` asserts the two agree.

WHY MARKDOWN, NOT PDF
    Heading-aware splitting is what produces the `section` metadata a citation
    needs. A `## 3.2 ...` heading in markdown is a reliable boundary; in a PDF it
    is a layout accident. The fee schedule also ships as a PDF to prove the loader
    handles one, but the markdown is the file that gets indexed.
"""
from __future__ import annotations

TERMS = """# PortCity Broadband — Terms of Service

Effective 1 January 2026. These terms govern residential broadband service
provided by PortCity Broadband Ltd. ("the operator") to a subscriber ("you").
They are read together with the Service-Level Agreement and the Fair-Usage Policy.

## 1. The service

### 1.1 What is provided
The operator provides residential internet access over a shared fibre and radio
network at the speed tier named in your subscription. The tier is a maximum, not
a guarantee: §2.3 of the Service-Level Agreement governs what happens when the
delivered speed falls short.

### 1.2 Connection and equipment
The operator supplies the optical network unit at the point of entry. Equipment
inside the premises — routers, switches, cabling beyond the entry point — is the
subscriber's responsibility unless it was supplied by the operator under a
maintenance plan.

### 1.3 Shared medium
Bandwidth on a node is shared among the subscribers on that node. Congestion at
peak hours is a property of the medium, not a fault. §2.4 of the Service-Level
Agreement describes when congestion becomes a fault.

## 2. Billing

### 2.1 Billing cycle
Service is billed monthly in advance. An invoice is issued on the first working
day of the month and is payable within fourteen calendar days.

### 2.2 Late payment
An invoice unpaid fourteen days after issue attracts a late fee of ৳100. The
operator may suspend service where an invoice is unpaid thirty days after issue.

### 2.3 Reconnection
Where service has been suspended for non-payment and is later restored, a
reconnection fee applies. The amount is set in the fee schedule (§7.1) and is
charged once per reconnection, not per suspension day.

### 2.4 Invoice disputes
A subscriber may dispute a line item within sixty days of the invoice date. A
disputed invoice is not suspended while the dispute is open. Disputes are raised
through the support desk; the desk records the item under dispute and the amount.

## 3. Changes to the subscription

### 3.1 Plan changes
A plan change takes effect at the start of the next billing cycle. Downgrades do
not attract a fee. Upgrades may attract a pro-rated charge for the remainder of
the current cycle.

### 3.2 Transfers and relocation
Where a subscriber moves within the service area, the subscription may be
transferred to the new address at no charge if the new address is served by the
same node. Where it is not, the subscription is treated as a new connection.

## 4. Suspension and termination

### 4.1 Suspension by the operator
The operator may suspend service for non-payment (§2.2), for a breach of the
Fair-Usage Policy, or where required by a lawful direction.

### 4.2 Termination by the subscriber
A subscriber may terminate with thirty days' written notice. Any credit due for
downtime under the Service-Level Agreement survives termination and is applied to
the final invoice.

## 5. Liability

### 5.1 Service credits as the remedy
Where the service fails to meet the Service-Level Agreement, the credit described
in that agreement is the subscriber's remedy. The operator is not liable for
consequential loss arising from an interruption.

### 5.2 Exclusions
The operator is not liable for interruption caused by an event outside its
control, by damage to equipment inside the premises, or by the subscriber's own
configuration.

## 6. Data and privacy

### 6.1 What is retained
The operator retains connection records, billing records and support contacts for
the period required by law. Traffic content is not retained.

### 6.2 Disclosure
Records are disclosed to a third party only where the subscriber consents or
where the law requires it.

## 7. Fees

### 7.1 Reconnection fee
A reconnection fee of ৳390 applies where service is restored after suspension for
non-payment. The fee is charged once per reconnection and appears as a separate
line on the next invoice.

### 7.2 Installation fee
A new connection attracts an installation fee set in the fee schedule. The fee
covers cabling to the entry point and the optical network unit.

### 7.3 Late fee
A late fee of ৳100 applies to an invoice unpaid fourteen days after issue (§2.2).

## 8. Changes to these terms

### 8.1 Notice
The operator may amend these terms with thirty days' notice. An amendment that
increases a fee applies from the next billing cycle after the notice period.

### 8.2 Governing document
Where these terms and the Service-Level Agreement disagree about downtime, the
Service-Level Agreement governs.
"""

SLA = """# PortCity Broadband — Service-Level Agreement (Residential)

This agreement sets the service levels the operator commits to for residential
subscribers, and the credit due when those levels are not met. It prevails over
the Terms of Service on any question about downtime (§8.2 of those terms).

## 1. Definitions

### 1.1 Downtime
Downtime is a continuous period in which the subscriber's line cannot pass
traffic, measured from the time the operator's monitoring or the subscriber's
report establishes the start, to the time service is restored. A period shorter
than fifteen minutes is not counted.

### 1.2 Incident
An incident is a single fault recorded against a node, identified by an incident
id (INC-nnnn). Multiple subscribers on the same node share one incident.

### 1.3 Continuous
Continuous means uninterrupted. Two separate interruptions on the same day are
two periods of downtime, not one, and each is assessed against §3.1 separately.

## 2. Service levels

### 2.1 Availability
The operator targets 99.5% monthly availability measured per node, excluding
planned maintenance notified at least 24 hours in advance.

### 2.2 Restoration target
The operator targets restoration within 8 hours of an incident being raised for
a node-level fault, and within 24 hours where the fault requires a cable works
crew.

### 2.3 Delivered speed
Where the delivered speed falls below 70% of the subscribed tier for more than
seven consecutive days, the subscriber may request a pro-rated credit for those
days. The request is assessed by a supervisor, not automatically.

### 2.4 Congestion
Peak-hour congestion on a node is not a fault unless the delivered speed falls
below 70% of the tier for seven consecutive days, which is assessed under §2.3.

## 3. Service credits

### 3.1 When a credit is due
A credit is due when a single continuous period of downtime exceeds four (4)
hours. Downtime of four hours or less does not attract a credit.

### 3.2 How the credit is calculated
The credit is pro-rated on the monthly subscription fee for the affected hours,
using a 24-hour day:

    credit = monthly_fee / 24 × downtime_hours

rounded to the nearest taka. The downtime hours used are those of the incident
period, counted to the minute. Where the same incident has already been credited
to the subscriber, no further credit is due for that incident.

### 3.3 How the credit is applied
A credit is applied to the subscriber's next invoice. It is never paid in cash
and never exceeds the monthly subscription fee for the affected month.

### 3.4 Approval
Every credit is proposed by the support desk and approved by a billing
supervisor before it is applied. The supervisor may approve, reduce or reject the
proposal. A rejection is recorded with a reason and is relayed to the subscriber.

### 3.5 One credit per incident
A subscriber receives at most one credit per incident. A second request for the
same incident is answered as already credited and is not paid again.

## 4. Exclusions

### 4.1 Scheduled maintenance
Planned maintenance notified at least 24 hours in advance is excluded from §3.

### 4.2 Subscriber-side faults
A fault in equipment inside the premises, or in the subscriber's own
configuration, is not downtime under §1.1.

### 4.3 Force majeure
An interruption caused by a declared natural disaster, a lawful direction, or
damage by a third party outside the operator's control is excluded from §3,
except that the operator will still restore service as soon as practicable.

## 5. Reporting and records

### 5.1 Incident record
Each incident carries the node, the start time, the estimated restoration time,
the cause where known, and the restoration time once restored.

### 5.2 Disputes
A subscriber may dispute an incident's start time within thirty days. The
operator's monitoring record is the reference unless the subscriber provides
evidence to the contrary.
"""

FUP = """# PortCity Broadband — Fair-Usage Policy

This policy sets out what a residential subscription may be used for. It is read
together with the Terms of Service.

## 1. Permitted use

### 1.1 Residential use
A residential subscription is for personal and household use at the subscribed
address. It is not for resale, for providing service to third parties, or for
operation as a business access point.

### 1.2 Devices
A reasonable number of household devices may be connected. The operator does not
limit the number of devices but may act where a single line is operated as a
shared access point for other premises.

## 2. Usage limits

### 2.1 Fair-usage threshold
A subscription has no monthly data cap. Where a line's usage is more than ten
times the median for its tier in a month, the operator may ask the subscriber to
explain the usage before acting.

### 2.2 Action on excessive use
Where usage remains disproportionate after an explanation, the operator may move
the line to a business tariff or suspend the line with thirty days' notice.

## 3. Prohibited activity

### 3.1 Unlawful use
The service may not be used for unlawful activity, including the distribution of
material that is illegal in Bangladesh.

### 3.2 Network abuse
The service may not be used to attack other systems, to send unsolicited bulk
mail, to operate an open relay, or to interfere with other subscribers' traffic.

### 3.3 Resale and sharing outside the premises
Reselling bandwidth, or extending service to another address, is prohibited and
is a ground for suspension under §2.2.

## 4. Security

### 4.1 Subscriber responsibility
The subscriber is responsible for the security of equipment inside the premises,
including the wireless password.

### 4.2 Reporting abuse
Suspected compromise of a line should be reported to the support desk promptly.
The operator may suspend a line to protect its network where abuse is active.

## 5. Enforcement

### 5.1 Proportionality
Enforcement is proportionate to the breach. A first breach of §3.2 generally
results in a warning and a requirement to remediate.

### 5.2 Appeal
A subscriber may appeal an enforcement decision through the support desk within
fourteen days.
"""

FEES = """# PortCity Broadband — Installation and Reconnection Fee Schedule

Effective 1 January 2026. These amounts are the ones the Terms of Service refers
to; the terms state when a fee applies, this schedule states how much.

## 1. New connections

### 1.1 Installation
Installation of a new residential connection is ৳1,500, which covers cabling to
the entry point and the optical network unit.

### 1.2 Installation waiver
Installation is waived where a new connection is taken at an address already
cabled by the operator and no new cabling is required.

## 2. Reconnection

### 2.1 Reconnection after suspension
Reconnection after suspension for non-payment is ৳390, charged once per
reconnection (Terms §7.1).

### 2.2 Reconnection after a move
Reconnection at a new address on the same node is free (Terms §3.2).

## 3. Changes

### 3.1 Plan upgrade
A mid-cycle upgrade is charged pro-rata for the remainder of the cycle at the new
plan's rate, with no administrative fee.

### 3.2 Plan downgrade
A downgrade is free and takes effect on the next cycle.

## 4. Other charges

### 4.1 Static IPv4 address
A static IPv4 address is a business product and is not available on a residential
subscription. Requests are referred to the business team.

### 4.2 Replacement equipment
Replacement of an optical network unit damaged by the subscriber is charged at
cost, quoted before the work.

### 4.3 Late fee
A late fee of ৳100 applies to an invoice unpaid fourteen days after issue
(Terms §7.3).
"""

DOCUMENTS = {
    "terms-of-service": TERMS,
    "service-level-agreement": SLA,
    "fair-usage-policy": FUP,
    "fee-schedule": FEES,
}


def write_all(directory) -> list:
    """Write the four documents, return the paths written."""
    from pathlib import Path

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for stem, text in DOCUMENTS.items():
        path = directory / f"{stem}.md"
        path.write_text(text, encoding="utf-8")
        written.append(path)
    return written


if __name__ == "__main__":
    from pathlib import Path

    here = Path(__file__).resolve().parent
    for p in write_all(here):
        words = len(p.read_text(encoding="utf-8").split())
        print(f"{p.name:32s} {words:5d} words")
