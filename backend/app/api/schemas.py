from pydantic import BaseModel
class ChatRequest(BaseModel): message:str
class MockCaseRequest(BaseModel): case_key:str
class ApprovalDecision(BaseModel): status:str; approver_user_id:str='agent-demo'; comment:str=''
class BadCaseCreate(BaseModel): trace_id:str|None=None; query:str; error_type:str; root_cause:str=''; expected_result:dict={}
