# User guide

How to use Trade-Lens, screen by screen. To install and start it, see the [README](../README.md#quick-start).
The live version is at https://statec.besser-pearl.org.

Trade-Lens has two pages, reached from the narrow menu on the left: **Overview** (the main page, with the
agents) and **Trends** (a workbench for one series at a time).

## 1. The landing page: who is reading?

The first time you open the app in a browser tab, the landing page asks **"Who is reading today?"**

- **Pick a reader.** Six readers are offered:

  | Reader | Who it is for |
  |---|---|
  | STATEC analyst | Official statistics: a figure you have to reconcile and quote |
  | Policy adviser | Ministries and public bodies: your next briefing note |
  | Journalist | Media: your next story |
  | Business owner | Trade and industry: your next export market |
  | Researcher | Academia and think tanks: your analysis |
  | Curious citizen | Everyone: what Luxembourg buys and sells |

  Each reader is a card. Point at a card to preview what the four agents could answer for that reader
  (example questions per agent, in the agent's pen colour); the box "Built on official Eurostat data" links
  to the dataset. Click the reader to choose it: the page is drawn fresh for that reader.
- **Or start with a question.** Click one of the example questions: the reader is chosen and the question is
  asked at once.
- **Or just ask.** Type your own question in "Or just ask, in your own words" and press **Ask**. If you pointed
  at a reader first, the question is asked as that reader; otherwise for every reader.
- **Not sure?** "Not sure? Show the standard page" opens the page for every reader.

What the reader changes: which charts open first (the others fold under "More for this view"), chart titles,
the suggested questions, how long and how technical the answers are, and whether product codes are shown.
What it never changes: the figures. Two readers asking the same question see the same numbers.

The landing page shows once per browser tab. To see it again, use **Change reader** at the top of the page,
or open a new tab.

## 2. The Overview page

At the top: the page title, the reader button (the reader's name and **Change reader**), and the toolbar:
**Undo**, **Redo**, **Clear my charts**, **Show the full overview** and **Export PDF**. Under it, "In short"
gives the headline figures of the year in one sentence.

The right-hand side holds the **Ask the data** panel. The rest of the page is your notebook: the charts the
agents drew for your questions, and, when you ask for it, the full overview.

### The full overview

**Show the full overview** adds the page's own charts: the three figures (imports, balance, exports), the
crossroads (where goods come from and go to, through Luxembourg), the trend since 1999, and a quiet row with
the export mix, Luxembourg's specialisation against EU peers, and its rank among them. **Back to my dashboard**
returns to the overview chart and your own charts only. This switch is your view only: it does not change the
page for anyone else.

## 3. Asking a question: the Ask panel

Type a question in plain words in the **Ask the data** panel and press Enter. Examples:

- "Why did exports jump in 2025?"
- "Who are Luxembourg's biggest customers?"
- "Show our exports to the United States. Show the months since 2023"
- "Is there a seasonal pattern in exports, by chapter?"
- "Compare Luxembourg with Belgium and Germany"
- "What can we expect for the next six months?"

**What happens.** A router chooses one to three agents for the question. Their cards light up in turn while
they work ("The agents are looking at the data."). Each agent reads the data through fixed methods, draws on
the page in its own pen colour, and replies in the panel. Agents that do not depend on each other work at the
same time; the cause finder waits for the trend finder's breaks, and the visualizer for the thread it builds
on.

| Agent | Pen | Typically draws |
|---|---|---|
| Trend finder | red | Heatmaps of months by year with trend breaks, the trend line, ranks over time, growth bars |
| Cause finder | purple | Event dates on a line, a waterfall of what made up a change |
| Forecaster | amber | A forecast band on a line, the seasonal shape of the year |
| Visualizer | green | Maps, flow maps, treemaps, bought-against-sold butterflies, comparisons, another year |

Data itself is always sky blue (exports) and steel grey (imports). Any other colour on a chart was drawn by
an agent.

**Reading a reply.**

- Each reply starts with the agent's name in bold. Numbers in circles, such as **[2]**, refer to the numbered
  marks on the page; click a number to bring that mark into view.
- **Chips.** Under the last reply, a suggested next question appears as a chip ("Suggested next question");
  click it to ask it. When the conversation is new, the panel offers suggested questions for your reader.
- **What happened.** Each reply has a "What happened" section: who answered, which calculations were run (by
  their plain names), what was drawn, how many figures in the reply were checked against the calculations, and
  a "For statisticians" part with the exact queries.
- **Notes.** Below the conversation, each numbered mark on the page has a note in the agent's colour. A note
  about another year than the one shown is dimmed and tagged ("about 2025").

**Typed commands.** Besides questions, the agents understand short commands: "keep 1", "remove 2", "Show me
2020" (moves every chart without a year of its own), "Clear the page". **Clear agent marks** in the panel
removes the agents' marks that you have not kept.

## 4. The notebook: questions as groups of charts

Every question you ask becomes a group on the page: "You asked" and the question, then the charts it drew,
side by side. The agents' explanation of those charts is folded away until you open it: **More about these
charts** under the group shows the replies and each chart's "How this was made" card (**Hide the explanation**
folds it again). **Add a note** lets you write your own note on the group: what you did there and what you
found (**Edit note** changes it). The note is printed with the group when you export the page.

The page holds a limited number of agent charts (eight when started with `run.py`). When it is full, a new
chart takes the place of the oldest chart you have not kept, in the same spot, and the reply says which chart
made room.

## 5. Working with a chart

Each chart has quiet controls in its header and under it.

- **Keep this chart** (the pin). A kept chart is never changed, replaced or removed by an agent. Click again to
  let the agents replace it. Agents may still add marks to a kept chart.
- **Remove** takes the chart off the page (hidden while the chart is kept).
- **The year and goods line** under the title ("2025, all goods", "2015 to 2025, machinery") opens a menu to
  give that chart its own Year (or From/To range) and Goods. **Follow the page** makes it follow the page again.
- **Clicking a bar, a country or a node** highlights it, so the agents know what you are looking at; click
  again to undo it.
- **Show as table** under a chart shows the same figures as a plain table.
- **How this was made** (see section 7). On a chart with a forecast, **Model card** as well.

**Undo and Redo** in the toolbar step back and forward through the page's changes (each agent batch, each
keep or remove is one step). **Clear my charts** (after a confirmation) removes your charts and notes that are
not kept; charts you kept stay. Like the overview switch, it acts on your view in this browser.

## 6. The charts

| Chart | What it shows | How to read it |
|---|---|---|
| Line | A series over time, monthly or yearly | Agents add a trend line, event dates (vertical rules with labels), trend breaks (a rule with the level before and after), and forecast bands |
| Seasonally adjusted switch | On monthly lines: **As recorded** or **Seasonally adjusted** | Adjusted takes the usual month-to-month pattern out (fitted on the whole monthly history since 1999), so a month can be compared with the one before. The recorded values stay as a faint line underneath. A series with less than five years of monthly history is not adjusted, and the chart says so |
| Forecast band | The next months or year, with an 80% band | The shaded zone after the data is the forecast; the band is where 80% of the method's past errors fell. For total exports or chapter 85 it can show two scenarios: the smartphone line stops or continues |
| Map (choropleth) | Partners coloured by share, value or balance | A balance map shades from steel (deficit) to sky (surplus) |
| Flow map | Arcs from Luxembourg to partners and back | Width is value; an agent's thread draws one route (for example bought from China, sold to France) |
| Heatmap | Months by years, products by years, or seasons by chapter | Darker is larger; trend breaks are outlined |
| Waterfall | What made up a change between two years | Starts at the first year's total, steps through the largest movers and "everything else", ends at the second year's total |
| Treemap | Goods as nested tiles, chapter then heading | Click a tile to open its chapter (drill-down); the breadcrumb takes you back |
| Growth bars | Growth per year since a start year (compound), fastest first | Each row gives the rate and the value in the first and last year; items with nothing at the start have no rate and are left out |
| Bars and drill-down | Rankings; several bars side by side for a drill-down from chapter (HS2) to heading (HS4) to the 8-digit line (CN8) | Only flows above a significance cutoff are listed (it drops one decimal per level of detail); the rest is "Other" |
| Butterfly | Imports on the left, exports on the right | The same partners or products on both sides |
| Bump chart | Ranks over the years | Lines cross when partners overtake each other |
| Scatter | Specialisation against growth | Above 1 on the specialisation axis, Luxembourg trades relatively more of that product than its EU peers |
| Comparisons | Luxembourg against EU countries, two partners, two years, exports against imports | Rebased (index), shares, year-on-year growth or differences; a balance chart shades the gap |
| Comparisons with a partner | Each EU country's **own** trade with one partner, for example exports to the United States by Luxembourg, Belgium, the Netherlands and Germany | Answers "is this specific to Luxembourg?". The EU countries' trade with the US, China, the UK and Switzerland is in the database (yearly from 2015, monthly from 2019); any other partner is fetched from Eurostat the first time |
| Tables with quantities | Value, **Quantity**, **€ per unit** and **Weight** for each partner or product | Quantity is the count in the goods' own unit (cars, litres, pairs...), Weight is in tonnes, € per unit and € per tonne are value divided by quantity: averages over models and grades, not prices. A quantity Eurostat does not publish reads **not published**, never 0 |
| Lines and bars of quantities | A count or a weight over time or by partner | The axis and the figures carry the unit ("37,654 cars", "122,464 t"); bars show each row's value in euros beside its count, and a line's tooltip gives the value in euros too |

### Quantities: how many, how heavy, how much per unit

Eurostat publishes three measures for each flow: the value in euros, the weight (net mass) and, for many
8-digit lines, a count in the line's own unit (number of items, litres, pairs, square metres...). Ask in plain
words: "How many cars did Luxembourg export in 2025?", "What is the average value of an exported car?", "Show
the weight of steel exports since 2015". The agents choose the measure: a count when the goods have their own
unit, else the weight.

- A whole chapter or total goods has a weight but no count (its lines are counted in different units); a
  heading (4 or 6 digits) is counted when its 8-digit lines share one unit.
- Unit values are averages, not prices, and "How this was made" says so.
- Quantities for Luxembourg's trade with the world, 2015 to 2025, are in the database; other selections are
  fetched from Eurostat the first time.

## 7. How this was made, and For statisticians

**How this was made** opens a card under any chart, text or note:

- **Method**: the calculation in plain words (for example "change-point analysis of the monthly series"), with
  a "Calculated" badge.
- **Calculated**: what was calculated, from which data.
- **Drawn by**: which agent drew it, or "The page" when no AI was involved.
- **The assistant**: what the language model did ("Chose this method and this chart, and wrote the title and
  the words.") and the statement that it did not calculate any figure.
- **For statisticians** (folded): the exact API query and the technical method, so you can rerun the figure
  yourself, for example with `curl` against `/insights/...`.

**Model card** (on charts with a forecast): the models in the forecast, what they were trained on, how they
were tested (a rolling backtest over quarter ends), the skill at 1, 3, 6 and 12 months against the "same month
last year" benchmark, and the other methods tried, including the BESSER neural networks.

## 8. Export PDF

**Export PDF** opens the browser's print dialog; choose "Save as PDF". Before printing, the page opens every
chart's "How this was made" card, so the PDF carries where each figure comes from and how it was calculated.
The PDF includes the questions, the agents' replies and your notes, with the reader and the source line.
Charts stay sharp because they are vector drawings.

## 9. Changing reader

**Change reader** at the top opens the reader screen again.

- Picking only a reader **keeps the current notebook** and reads it as the new reader (the panels and words
  change, the figures do not).
- Starting with a question from that screen opens a **new** notebook and a new conversation.
- To compare two readers side by side, open a new browser tab for each.

## 10. The Trends page

**Trends** in the left menu (or `/trends`) analyses one series at a time.

- **The series**: choose the product, the reporter (Luxembourg or an EU country), the partner and the flow.
- **The series and its trend**: the trend from an STL decomposition, with **As recorded** or **Seasonally
  adjusted** above the chart.
- **Summary**: direction, trend strength, seasonality and the number of change points.
- **Change points**: where the average level shifted and stayed shifted, with the level before and after. The
  sensitivity slider finds more breaks to the left, fewer to the right.
- **Seasonality**: the typical shape of a year, and how much of the movement is seasonal.
- **Seasons of the main products**: a heatmap of the twelve largest chapters, month by month.
- **Forecast**: the next months with an 80% band, against "same period a year earlier". It needs at least 36
  months of consecutive monthly history.
- Each section has its own **For statisticians** part with the query.

## 11. When something is slow or does not answer

| What you see | Why, and what to do |
|---|---|
| A live answer takes 10 to 40 seconds | Normal for a question the agents have not seen: the router plans, each agent reads the data and draws. The first answer after the backend starts can take about 20 seconds. Keep reading the page; the cards show which agent is working |
| "Agents not reachable" in the Ask panel | The agent is not running. Without `.venv-agent` and a key in `.env`, `run.py` starts the page without it and says what to set up. The charts, the Trends page and the API still work |
| The first forecast takes up to about 40 seconds | After a fresh start, the backend trains the pooled gradient-boosting models in the background (once; they are then kept on disk). Later forecasts take about 2 seconds the first time for a series and much less after that |
| A chart takes a few seconds on first view | The slice was not in the database and is fetched from Eurostat's API (for example a partner month by month). It needs network access, and it is fast the next time |
| "Too short to adjust" on the seasonally adjusted switch | The series has less than 60 months (five years) of monthly history; it stays as recorded |
| A chart shows nothing for a year | There is no data for that selection and year; choose another year in the chart's menu |
| A reply says a chart took the place of another | The page holds a fixed number of agent charts. Keep the ones you want with the pin |
| The page looks stuck after many questions | Use **Undo**, or **Clear my charts**, or start a fresh notebook from **Change reader** with a question |
| Port already in use when starting | Another program holds port 8000 or 3000: `python run.py --port 8400 --frontend-port 3400` |
