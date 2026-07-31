import logging
from asgiref.sync import sync_to_async
from typing import Dict

from apis.models import Question, ModelResponse
from apis.engine.state import OrchestrationState
from apis.engine.context import ExecutionContext
from apis.engine.graph_def import GraphDefinition
from apis.engine.engine import GraphEngine
from apis.engine.nodes import draft_node, peer_review_node, correction_node, synthesis_node

from apis.agents.gemini_agent import GeminiAgent
from apis.agents.nvidia_agent import NvidiaAgent
from apis.agents.openrouter_agent import OpenRouterAgent
from apis.services.judge import ResponseJudge

logger = logging.getLogger(__name__)

# Keep a cache of DB response records mapped by (question_id, agent_name)
# to update them cleanly across node executions
_db_response_cache: Dict[tuple, ModelResponse] = {}

def build_think1by0_graph() -> GraphDefinition:
    """Assembles and returns the declarative Think1by0 orchestration graph."""
    graph = GraphDefinition()
    
    # 1. Register Nodes
    graph.add_node("draft", draft_node)
    graph.add_node("review", peer_review_node)
    graph.add_node("correct", correction_node)
    graph.add_node("synthesize", synthesis_node)
    
    # 2. Define Flow Structure (YAGNI sequential flow for now, easily changeable)
    graph.set_entry_point("draft")
    graph.add_edge("draft", "review")
    graph.add_edge("review", "correct")
    graph.add_edge("correct", "synthesize")
    
    return graph


class OrchestrationGraph:
    """
    Entry point for running the Think1by0 orchestration.
    Instantiates environment resources (APIs, DB managers) and runs the Engine.
    """
    def __init__(self):
        # Initialize resources
        self.agents = {
            'gemini': GeminiAgent(),
            'nvidia': NvidiaAgent(),
            'openrouter': OpenRouterAgent()
        }
        self.judge = ResponseJudge()
        self.graph_def = build_think1by0_graph()
        self.engine = GraphEngine(self.graph_def)

    async def run(self, question_id: int) -> OrchestrationState:
        """Runs the orchestration engine with DB persistence hooks."""
        # 1. Fetch Question (Async DB call)
        question = await sync_to_async(Question.objects.get)(id=question_id)
        
        # 2. Initialize State and Context
        initial_state = OrchestrationState(question_id=question_id, prompt=question.prompt)
        context = ExecutionContext(
            agents=self.agents,
            judge=self.judge,
            config={"score_threshold": 7.0, "master_agent": "gemini"},
            logger=logger
        )

        # Clear cache for this run
        _db_response_cache.clear()

        # Define DB persistence callbacks to keep nodes and engine completely pure
        async def handle_node_end(node_name: str, state: OrchestrationState):
            await self._persist_node_state(node_name, state, question)

        # 3. Execute Engine
        return await self.engine.execute(
            initial_state=initial_state,
            context=context,
            on_node_end=handle_node_end
        )

    async def _persist_node_state(self, node_name: str, state: OrchestrationState, question: Question):
        """Helper to write node updates to Django models asynchronously."""
        try:
            if node_name == "draft":
                for name, agent_state in state.agent_states.items():
                    db_resp = await sync_to_async(ModelResponse.objects.create)(
                        question=question,
                        model_name=name,
                        response_text=agent_state.draft or "No draft generated."
                    )
                    _db_response_cache[(state.question_id, name)] = db_resp
                logger.info("Drafts persisted to database.")

            elif node_name == "review":
                for name, agent_state in state.agent_states.items():
                    db_resp = _db_response_cache.get((state.question_id, name))
                    if db_resp:
                        db_resp.score = agent_state.score
                        db_resp.critique = agent_state.critique or ""
                        await sync_to_async(db_resp.save)()
                logger.info("Peer review scores persisted to database.")

            elif node_name == "correct":
                for name, agent_state in state.agent_states.items():
                    db_resp = _db_response_cache.get((state.question_id, name))
                    if db_resp:
                        db_resp.final_answer = agent_state.final_answer or ""
                        # If correction occurred, update to the new score and critique
                        if agent_state.corrected_answer:
                            db_resp.score = agent_state.score
                            db_resp.critique = agent_state.critique or ""
                        await sync_to_async(db_resp.save)()
                logger.info("Self-corrections persisted to database.")

            elif node_name == "synthesize":
                question.final_answer = state.blended_result or ""
                question.critique = "Collaborative StateGraph Orchestration and self-correction cycle completed."
                await sync_to_async(question.save)()
                logger.info("Final synthesized answer persisted to database.")
                
        except Exception as e:
            logger.error(f"Failed to persist state for node {node_name}: {e}")
