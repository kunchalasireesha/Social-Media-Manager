your task is to create a stategraph in the @smm.ipynb 
take the reference of @reference/4-AdaptiveRAG.ipynb to construct the stategraph.
I had given the stategraph diagram in the file @reference/smm.png
What each stage of the graph should do is mentioned below. 

Data Extractor: 
    - go through the input.txt file. 
    - parse the data into the profile_metadata and smm_metadata models.
    - Hit the apify api to get the profile data for the subject and competitors and the important accounts.
    - All of this code is already there in the @smm.ipynb file. you just need to format the code into the functions to work with the stategraph.

Account scrapper and prompt modifier: 
    - Hit the apify api to get the profile data for the subject and competitors important accounts. 
    - Add the usernames of the subject, competitors and important accounts to the profile_metadata and smm_metadata models.
    - All of this code is already there in the @smm.ipynb file. you just need to format the code into the functions to work with the stategraph.

Execute Prompt 1: 
    - This phase is called as the DNA intake. 
    - Execute the prompt 1 to get the DNA intake.
    - Store the result returned from the prompt 1 in the stategraph. We need to carry forward this data to the next data. 

Execute Prompt 2: 
    - This phase is called as the instagram content intelligence engine.
    - Execute the prompt 2 to get the instagram content intelligence. The prompt is present in the file @prompts/instagram-content-intelligence-engine.md
    - Store the result returned from the prompt 2 in the stategraph. We need to carry forward this data to the next data. 

Execute Prompt 3: 
    - This phase is called as the live performance learning engine.
    - Execute the prompt 3 to get the live performance learning. The prompt is present in the file @prompts/live-performance-learning-engine.md
    - Store the result returned from the prompt 3 in the stategraph. We need to carry forward this data to the next data. 

Execute Prompt 4: 
    - This phase is called as the content opportunity prioritization engine.
    - Execute the prompt 4 to get the content opportunity prioritization. The prompt is present in the file @prompts/content-opportunity-prioritization-engine.md
    - Store the result returned from the prompt 4 in the stategraph. We need to carry forward this data to the next data. 

Execute Prompt 5: 
    - This phase is called as the performance-script-execution-engine.
    - Execute the prompt 5 to get the performance-script-execution. The prompt is present in the file @prompts/performance-script-execution-engine.mdq
    - Store the result returned from the prompt 5 in the stategraph. We need to carry forward this data to the next data. 

Execute Prompt 6: 
    - This phase is called as the content optimization engine.
    - Execute the prompt 6 to get the content optimization. The prompt is present in the file @prompts/content-optimization-engine.md
    - Create a cleanly formatted document of the output of the prompt 6. 