1) We finish ingesting the data layer for the rates agent - everything ranging from swaps, relevant bond prices/yields data, swap rates, etc etc. by populating our playbooks accordingly and taking in the data by running our extraction pipeline. 
2) And then we build out the rates agent deterministic tool/ math layer - which is our PCA, Z score tools etc etc. Goal of this step should be to automate all of the 'low hanging fruit', basically all the determinsitic tasks that an entry level intern or analyst would do in rates. 
3) then we build the LLM layer and a basic UI so the user can ask all of these entry level tasks like give me the last 3 year PCA or some shit like that for e.g. or whatever else relevant to the tools we are building. 
4) then we replicate this for 3/4 other important agents, such as FX, monetary policy agent and whatever else is important initially. 
So by step 4, we will have all of the 'low hanging fruit', essentially the intern layer for the most important agents finished. 
then we move onto much harder stuff if we are able to finish these (which i am confident we can due to its deterministic nature): 
Harder stuff, which is still not yet planned/ which i still don't have a very strong idea on what is possible or what sort of workflows i am building here, but this would potentially include: 
1) Non linear products such as derivatives, options modeling, swaptions modeling, volatility surface modeling etc. 
2) non deterministic answers such as regime modelling, HMM models, some sort of systematic macro modeling perhaps. 
3) Multi agent reasoning and architecture using LLMs + deterministic model from individual agent outputs. 
4) some forms of time series ML models to make sense of macro data continuing from step 5 
5) interpreting unstructured data, speech and text transcripts from the terminal to score them using semantics and NLP and LLMs to make sense of central bank speeches, market expectation mapping etc 

one thing i am particularly concerned about is whether the derivatives and stuff should come within each agent, for e.g. would it make sense to have the swaptions data within the rates agent, fx options data within the FX agent etc. or should we have a dedicated agent for it, which i mentioned as point number 5? or should we have a hybrid where a dedicated derivatives agent has the mathematical capacity to dive deep into individual derivative data from the individual agents? 
also by derivatives modeling, i mean stuff that is relevant to macro trading. 

Phase 1: Automating the Intern (Steps 1 - 4)

This phase alone is incredibly valuable. In institutional finance, PMs spend 40% of their day asking junior analysts to "pull the 5-year Z-score for the US, UK, and Germany." Automating this into a deterministic, conversational LLM layer is exactly what funds are spending millions to build right now.

Step 1 (The Data): You are 100% correct. One month of data is useless for PCA. You need 15-20 years to capture different hiking/cutting cycles. Expanding the playbooks to grab decades of history for cash bonds, OIS, and SOFR/Euribor swaps is the immediate next move.

Step 2 (The Math): Perfect. Deterministic tasks only.

Step 3 (The LLM UI): This is where you prevent hallucinations. The LLM does not calculate the PCA. The LLM translates the user's English ("Give me the 3-year PCA") into a Python function call, runs your deterministic script, and formats the output.

Step 4 (Horizontal Scaling): FX, Central Bank Policy, and Commodities. Once you have the template from Rates, scaling this is just copy-pasting your ingestion scripts and changing the Bloomberg tickers.

The Derivatives Architecture Question (Step 5)

You asked: Should derivatives sit within their specific asset class agents (Rates, FX), or be their own dedicated agent?

The Institutional Answer: The Hybrid Matrix.

In macro, volatility is considered its own asset class, but it is derived from local domains. If you put all options in a single silo, you lose context. If you separate them completely, you lose cross-asset relative value.

Here is how you build it:

Data & Local Pricing lives in the Domain Agent: The Rates Agent ingests swaption data and knows how to calculate implied normal volatility for a 1y10y swaption. The FX Agent ingests 1M25D risk reversals and knows how to price an FX straddle.

The "Macro Volatility Agent" sits above them: You build a dedicated Volatility Agent that queries the other agents.

Why do it this way? Because a Macro PM rarely looks at a swaption in a vacuum. A classic macro trade is cross-asset hedging. A PM will ask: "I want to hedge against the Fed hiking rates unexpectedly. Is it cheaper to buy US payer swaptions, or is it cheaper to buy USD/JPY call options?" To answer that, you need a Volatility Agent that can talk to both the Rates Agent and the FX Agent, normalize their volatility surfaces into a common metric (like Z-score of implied vol), and spit out the cheapest hedge.

Phase 2: The Alpha Layer (Steps 6 - 9)

Once your "interns" are built, you move to the "Harder Stuff." Your list is highly accurate, but I would reorder your priorities based on what actually moves the needle in Macro:

Move Step 9 Up (NLP & Central Banks): Do this right after Step 4. Parsing unstructured central bank speeches using LLMs to create a "Hawkish/Dovish Index" is the most powerful low-hanging fruit in modern macro. It bridges the gap between deterministic data and human policy.

Step 6 (HMMs & Regime Modeling): This is the holy grail. Once you have decades of Rates, FX, and Volatility data, you train Hidden Markov Models to identify if the market is currently in a "Stagflation," "Goldilocks," or "Recession" regime.

Step 7 (Multi-Agent Reasoning): This is the final boss. The PM Agent. It asks the Policy Agent for the hawkishness score, the Rates Agent for the yield curve slope, and the Volatility Agent for the cheapest hedge. It synthesizes a trade recommendation based on the current HMM regime.