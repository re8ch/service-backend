#!/usr/bin/env python3
import os, textwrap, types, unittest

PATH = os.path.join(os.path.dirname(__file__), "../manifests/k3s/egress-fabric/config.yaml")
raw = open(PATH).read().split("  path-controller.py: |\n", 1)[1]
source = textwrap.dedent(raw)
controller = types.ModuleType("controller")
exec(compile(source, PATH, "exec"), controller.__dict__)

class ChooseTest(unittest.TestCase):
  def test_first_choice(self):
    self.assertEqual(controller.choose({"la": 30, "kl": 60}, None, 0, 100)[0], "la")
  def test_hold_prevents_flap(self):
    self.assertEqual(controller.choose({"la": 100, "kl": 10}, "la", 90, 100)[0], "la")
  def test_margin_prevents_small_switch(self):
    self.assertEqual(controller.choose({"la": 100, "kl": 90}, "la", 0, 1000)[0], "la")
  def test_material_improvement_switches(self):
    self.assertEqual(controller.choose({"la": 100, "kl": 70}, "la", 0, 1000)[0], "kl")
  def test_loss_penalty_decays(self):
    scorer = controller.Scorer()
    scorer.observe("la", 100, False)
    failed = scorer.score("la")
    for _ in range(8): scorer.observe("la", 100, True)
    self.assertLess(scorer.score("la"), failed)

if __name__ == "__main__": unittest.main()
