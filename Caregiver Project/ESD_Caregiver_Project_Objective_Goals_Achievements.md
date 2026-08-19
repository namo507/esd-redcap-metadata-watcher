# The Caregiver Project, explained simply

**Early Social Development Lab, University of South Carolina**
Plain-language summary of objective, goals and achievements
Prepared 19 August 2026

---

## The whole thing in one breath

Doctors might one day be able to check a four month old baby for autism. That only works if parents actually say yes. So we asked parents how they feel about it, we sorted them into groups by how they answered, and then we checked whether those feelings match what they say they would really do. Along the way we found that a big pile of our survey answers could not be trusted, so we built a way to tell the good answers from the bad ones before we believed anything.

## If you are five

Imagine you want to give every baby a quick check up to see if they might need extra help learning to talk and play. You cannot just decide that on your own. You have to ask the mums and dads first.

So we asked about two thousand grown ups how they felt about it.

We found out two things.

**Thing one.** Grown ups come in two flavours. Some are happy about the check up straight away. Others say yes but only if you answer their questions first. The second group is not saying no. They are saying not yet.

**Thing two.** Some of the answers we got were not from real careful grown ups. We could tell because a few of them said impossible things, like saying they have two children and then ticking three different age boxes. Two children cannot be in three age groups. So we built a rule that spots those, and we only trusted the answers that made sense.

---

## Objective

The objective is to find out whether caregivers would accept early autism screening for infants, and to be sure the data we used to answer that question is real.

Infant autism screening does not exist as a routine service yet. Before anyone builds one, somebody has to know whether caregivers would walk through the door. That is what this project is for. The second half of the objective is not optional decoration. One of our two survey projects was flooded with low quality submissions, and a finding built on junk data is worse than no finding at all, so proving the data is trustworthy is part of proving the result.

## Goals

**Goal 1. Find out if caregivers split into distinct attitude groups.**
Not "how supportive is the average parent", which averages away everything useful, but whether there are recognisably different kinds of response a clinic would actually meet.

**Goal 2. Test those groups against something we deliberately kept out of the grouping.**
The screening question and the timing question were held back on purpose. If we had used them to build the groups and then compared the groups on them, we would have been arguing in a circle.

**Goal 3. Work out who ends up in each group.**
Is it education, income, area, autism knowledge, or already having an autistic child at home? If a background variable predicts attitude, a programme could plan around it.

**Goal 4. Separate trustworthy survey records from untrustworthy ones, using fixed rules set in advance.**
No eyeballing, no judgement calls after seeing the answers.

**Goal 5. Prove the main finding does not depend on which records we keep.**
If the answer changes when we tighten or loosen the rules, it was never a finding.

**Goal 6. Write down what we can honestly claim, and what we cannot.**

---

## Achievements

### Chapter one: the full pipeline and the trust screen

I pulled 1,956 records across the two REDCap projects and put every single one through the same ten fixed rules, and I think the most important thing this chapter produced is not a number at all, it is the ability to say where each record sits on a four step trust ladder instead of slapping a real or fake label on it. In the supervised project, 153 of 177 records (86.4 percent) passed every check. In the legacy project, only 70 of 1,779 did, which is 3.9 percent. That gap is why the supervised project became the main dataset and the legacy one became a cautious second opinion rather than an equal partner.

The rule I am proudest of is the branching logic check. It does not say a record looks odd, which is always arguable. It says the survey form could not have produced this answer, which is not. A caregiver who reports two children and then ticks three different age bands has given us something the form does not permit. That check flagged 44 records, all 44 of them in the legacy project, none in the supervised one, and it flagged zero of our 131 verified human caregivers, so tightening it cost us no good data at all.

On the attitude side, 131 caregivers answered enough of the ten attitude domains to be grouped, and 127 of them also answered the screening question. Two groups came out. The larger one (84 caregivers, 64.1 percent) sits above average on every domain. The smaller one (47 caregivers, 35.9 percent) sits below on every domain. When I looked at the held out screening question, 70.0 percent of the first group said definitely yes against 27.7 percent of the second, a gap of 42.3 percentage points, and the confidence intervals do not touch.

I then asked the obvious sceptical question, which is whether this is really just families who already live with autism. It is not. After adjusting for having an autistic child at home, belonging to the more supportive group still carried about 8.1 times the odds of saying definitely yes (Firth confidence interval 3.5 to 20.8), while having an autistic child at home carried about 4.1 times the odds on its own (1.8 to 10.2). Both matter, and they appear to simply add up rather than feed off each other.

Finally I re ran the entire analysis five separate times under five different rules about which records to keep, ranging from the strictest clean subset to a legacy only replication. The gap between the two groups landed between 42.3 and 52.2 percentage points every single time, and never came close to zero. That is the sentence that lets me say the finding is real rather than an artefact of a cleaning choice.

### Chapter two: the newer profiling notebook

The second analysis rebuilt the whole thing from the cleaned file with a tighter, more transparent method, and what I like about it is that every choice is written down before the numbers appear. It took 34 attitude items, checked which ones actually measure the same underlying thing using Cronbach's alpha, and collapsed them into six named domains. It threw out items where more than 85 percent of people gave the same answer, because an item everyone agrees on cannot separate anybody. Of 135 cleaned records, 4 had not completed the attitude instrument and 24 skipped at least one item used in the final features, leaving 107 caregivers.

Two profiles again. Ready and Willing, ethically confident (63 caregivers, 58.9 percent) and Hesitant, ethically uncertain (44 caregivers, 41.1 percent).

The result I would put on the first slide of any talk is the timing one, because it changes what a clinic should actually do. Of the ready group, 84 percent would screen now or would have done it earlier, against 34 percent of the hesitant group. But here is the part that matters. Among the hesitant caregivers, 59 percent said they would wait and only 7 percent said they would decline outright. Almost nobody is refusing. They are deferring. A single yes or no uptake number would have hidden that completely, and it is the one distinction a programme could genuinely act on, because someone who wants to wait has already accepted the premise and is negotiating timing, while someone who declines is rejecting the premise itself.

The hesitant group also demands more of the test before they will use it. They want higher accuracy before agreeing, with a mean of 2.23 against 2.70 on a scale where 1 means they insist on 98 to 100 percent accuracy. So their hesitance is partly about confidence in the test, not only about feelings toward screening. The notebook also reports that they would believe a positive result less, and I would check the coding direction on that one item before repeating it, because the reverse coding log and the reported means point in opposite directions.

And then there is the finding that is a negative one, which I think is the most useful thing in the section. Almost nothing about a caregiver's background predicts which profile they land in. Autistic children are present in 43 to 46 percent of both profiles. Autism knowledge did not separate them at the conventional threshold (p = 0.063), and neither did healthcare access, self rated knowledge, education, income, area, gender or child special needs. Only two context variables crossed p < 0.05 at all, and those were exploratory with no correction for multiple testing. The practical consequence is blunt. You cannot guess a caregiver's stance from their demographics. Any plan that tailors the screening conversation by segment will misroute a large share of families. You have to ask.

### What both chapters agree on

Both analyses, run on different inclusion rules with different domain structures, land on two groups, both find the more supportive group is the larger one, and both find a very large difference in willingness that survives every check thrown at it. That convergence is worth more than either analysis alone.

---

## What I will not claim

**I will not say attitudes cause screening decisions.** We measured both in the same sitting, so I have a link, not a direction.

**I will not put a number on how many records were bots.** The model produced one, but the assumptions behind it do not hold in this recruitment setting, and I would rather report a trust tier than a fabricated prevalence. We also have no way to know who or what typed any answer.

**I will not say the groups are the same on knowledge or demographics.** I could not detect a difference, which is a different sentence. At this sample size, a gap smaller than about 25.6 percentage points would have been invisible to us.

**I will not say the 44 flagged records measure how bad the legacy project is.** They tell me about those 44 records and nothing more.

**I will not present the profiles as caregiver types.** The separation is weak (silhouette 0.24), and a different clustering algorithm, Ward linkage, put people in noticeably different groups (adjusted Rand index 0.21). Caregivers sit on a continuum of support. Two profiles is one reasonable way to summarise that continuum, not a discovery of two natural kinds of person.

---

## One thing to sort out before publication

The two analyses do not use the same analytic sample. The pipeline keeps 131 caregivers by requiring at least 8 of 10 domains, and splits them 84 to 47. The newer notebook keeps 107 by requiring complete cases on 6 domains, and splits them 63 to 44. Both are defensible, and they agree on the direction and rough magnitude of everything that matters, but they will report different headline percentages. I would pick one as primary and present the other as a sensitivity check rather than letting both float, because a reviewer will notice.

---

## Where each number lives

| Claim | Source file |
| --- | --- |
| 1,956 records, two projects | `config.yaml`, `table_16_trust_tier_counts.csv` |
| Trust tier pass rates (86.4% vs 3.9%) | `table_16_trust_tier_counts.csv` |
| 44 impossible records, all legacy | `table_15b_rule_counts_by_project.csv`, `table_34_branching_logic_audit.csv` |
| Zero false positives on 131 humans | `table_15_rule_false_positive_rates.csv` |
| Cohort flow 135 to 131 to 127 | `table_5b_cohort_flow.csv` |
| 70.0% vs 27.7% definitely yes | `table_5_screening_outcome.csv` |
| Odds ratios 8.1 and 4.1 | `table_22_logistic_models.csv` |
| Gap holds at 42.3 to 52.2 points | `table_20_tier_sensitivity.csv` |
| Minimum detectable difference 25.6 points | `table_29_power_precision.csv` |
| Profiles 63 and 44, timing splits | `table_06_headline_summary.csv` |
| Silhouette 0.24, Ward ARI 0.21 | `table_03_cluster_validation_metrics.csv`, notebook §8 |
| Post cluster comparisons | `table_05_post_cluster_comparisons.csv` |
| Decisions D1 to D5 | `table_30_decision_summary.csv` |
