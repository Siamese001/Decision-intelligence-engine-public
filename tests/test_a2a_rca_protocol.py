import unittest

from dual_pipeline.a2a_protocol import A2AEnvelope, dispatch_a2a_message


class TestA2AProtocol(unittest.TestCase):
    def test_a2a_envelope_serialization(self):
        env = A2AEnvelope(
            task_id="rca_123",
            sender="agent_a",
            recipient="agent_b",
            hypothesis="High contention on file X",
            evidence_refs=["fileX.py"]
        )
        data = env.to_dict()
        self.assertEqual(data["task_id"], "rca_123")
        self.assertEqual(data["sender"], "agent_a")

        env2 = A2AEnvelope.from_dict(data)
        self.assertEqual(env2.message_id, env.message_id)
        self.assertEqual(env2.task_id, env.task_id)

    def test_dispatch_a2a_message(self):
        env = A2AEnvelope(
            task_id="rca_456",
            sender="orchestrator",
            recipient="panel",
            hypothesis="Consolidate branch A and B"
        )
        res = dispatch_a2a_message(env)
        self.assertEqual(res["status"], "DELIVERED")
        self.assertEqual(res["envelope"]["task_id"], "rca_456")

if __name__ == '__main__':
    unittest.main()
