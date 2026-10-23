
I'll implement the TX00/TOSS DATA_ONLY changes as specified in the ticket. Let me start by reading the relevant source files to understand the current implementation.














Based on the ticket requirements, I need to implement the TOSS DATA_ONLY restrictions. Let me start by examining the current implementation and making the required changes.




Let me start implementing the required changes based on the ticket specifications. I'll begin with modifying the TossBrokerAdapter.submit_order method.


Now I'll add the early GET-only mutation gate to the TossAuthClient.request method.


Now I'll add the early GET-only mutation gate to the TossAuthClient._request_once method.


Now I need to create the new test file as specified in the ticket. Let me create the test file for TX00 TOSS DATA_ONLY.


Now I need to read and modify the existing test file to split the GET refresh cases from the mutation-policy case as specified in the ticket.






Let me try to run the pytest command to understand the current test structure and then proceed with the implementation.


Great! Now I can see the test structure. The tests are failing as expected because my changes broke the existing POST functionality, which is exactly what we want. Let me fix the test issues and update the existing tests to split GET and POST cases.
