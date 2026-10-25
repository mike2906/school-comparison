# Yearly cost table

The compare page's "Cost per year" section (`frontend/src/utils/yearCost.js`, rendered by
`frontend/src/components/ComparePage/yearCostSection.jsx`) shows what one year at each private
school costs for one child, line by line, from the fee rows we already publish. It is step 1
of the yearly cost pilot; nothing here reads websites or calls a model.

## What a parent sees

For the stage they pick (a kindergarten group, the preschool year or grades 1–12; the default
comes from the saved child when it fits the searched age group, else that group's first year, else grade 1):

| Line | From | In "Required each year" |
|---|---|---|
| Tuition | the stage's whole, regular tuition (`tuitionRows`), yearly rows first | yes, only when stated per year |
| Books & materials | `materials` rows | yes, unless the fee says it is optional |
| Other required fees | rows the school calls compulsory (`requiredExtras`): 635's club | yes |
| Food | `food` rows, or "included" from the tuition's words | always (every child eats), when included or one yearly price; else named as not added |
| After-school care, school bus | `extended_day`, `transport` rows | only when ticked, and only when they come to one yearly price |
| First year only | admission fees, each listed (two may be one or the other, so no sum); deposits and uniform item prices apart | no |
| Optional extras | clubs, activities, camps the school prices | never |

Each price links to its source page. A line is "Included in tuition" only when every tuition
row for the stage says so (includes list or notes), "Not in tuition" when a row says it is
left out, else "Not stated" (for food, "Not stated whether tuition includes it"). Food is
counted so a fee that leaves it out does not look cheaper than one that holds it. The ticks are saved on the device (`costNeeds` in
`src/admission/saved.js`), so every comparison adds them without asking again.

## On the school list

A private school's card adds what its headline fee leaves out (`feeOnTop`): "food included",
"+ food, materials" or "food cost not stated". A printed total gets no marker. The list still
sorts by tuition: on 25 Oct 2026 only 2 of the 26 schools with a yearly grade 1 fee had food
known as one yearly price, so a sort by yearly cost would rank partial totals as cheaper.

## Rules (from the design review, `/mnt/project-files/yearly-cost/design.md` Revision)

- No made-up figures: a monthly tuition gets no yearly total ("billed months not published"),
  and a missing line stays "Not stated", never a typical value.
- A fee with no stated period counts once a year only on a page whose tuition is yearly, and
  the total then carries an asterisk saying so. Notes that say "per school year" count as yearly.
- Several options for one line (lunch at either campus) are the parent's choice: the line is
  listed but not added, and the total says what it left out. A one-way bus beside a two-way
  one gives way to the two-way price.
- A printed total of several fees is the required total on its own; ticked lines are not
  added on top, as they may be inside it.
- The same fee read from two pages is counted once; a row naming no group, from a fee page
  whose tuition is only for other grades, is not this stage's (596's secondary-school PDF).

## Known gaps (need the reader, not the table)

- Billed months for monthly fees (about 30 kindergartens) and paid hours for after-school care.
- "Included" is read for only a few schools: 172's grade 1 tuition includes food on its site,
  but our row does not say so.
- Many food, bus and club rows have no period (`period: null`), and many food prices are per
  day or month (no billed days).
- Whether the tuition includes food: the fee reader has no field for it, so it is known only
  where a note says so. Hand check of 16 grade 1 fee pages (25 Oct 2026): 172, 593 and 568
  include food; 271 and 171 bill it apart with no price; 350, 381 and 630 price it per day or
  month (350 and 381 also allow food from home).

These wait for the pilot re-read in the design doc. Sorting the list by yearly cost waits for
an audited accuracy check.
