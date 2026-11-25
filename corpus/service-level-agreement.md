# PortCity Broadband — Service-Level Agreement (Residential)

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
