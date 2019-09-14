# coding=utf-8
# Copyright 2018 The TF-Agents Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Tests for tf_agents.bandits.agents.neural_linucb_agent."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from absl.testing import parameterized
import numpy as np
import tensorflow as tf
import tensorflow_probability as tfp
from tf_agents.bandits.agents import neural_linucb_agent
from tf_agents.bandits.agents import utils as bandit_utils
from tf_agents.bandits.drivers import driver_utils
from tf_agents.networks import network
from tf_agents.specs import tensor_spec
from tf_agents.trajectories import policy_step
from tf_agents.trajectories import time_step
from tensorflow.python.framework import test_util  # pylint: disable=g-direct-tensorflow-import  # TF internal


tfd = tfp.distributions


class DummyNet(network.Network):

  def __init__(self, name=None, obs_dim=2, encoding_dim=10):
    super(DummyNet, self).__init__(name, (), 'DummyNet')
    self._layers.append(
        tf.keras.layers.Dense(
            encoding_dim,
            kernel_initializer=tf.compat.v1.initializers.constant(
                np.ones([obs_dim, encoding_dim])),
            bias_initializer=tf.compat.v1.initializers.constant(
                np.zeros([encoding_dim]))))

  def call(self, inputs, unused_step_type=None, network_state=()):
    inputs = tf.cast(inputs, tf.float32)
    for layer in self.layers:
      inputs = layer(inputs)
    return inputs, network_state


def test_cases():
  return parameterized.named_parameters(
      {
          'testcase_name': '_batch1_contextdim10',
          'batch_size': 1,
          'context_dim': 10,
      }, {
          'testcase_name': '_batch4_contextdim5',
          'batch_size': 4,
          'context_dim': 5,
      })


def _get_initial_and_final_steps(batch_size, context_dim):
  observation = np.array(range(batch_size * context_dim)).reshape(
      [batch_size, context_dim])
  reward = np.random.uniform(0.0, 1.0, [batch_size])
  initial_step = time_step.TimeStep(
      tf.constant(
          time_step.StepType.FIRST, dtype=tf.int32, shape=[batch_size],
          name='step_type'),
      tf.constant(0.0, dtype=tf.float32, shape=[batch_size], name='reward'),
      tf.constant(1.0, dtype=tf.float32, shape=[batch_size], name='discount'),
      tf.constant(observation, dtype=tf.float32,
                  shape=[batch_size, context_dim], name='observation'))
  final_step = time_step.TimeStep(
      tf.constant(
          time_step.StepType.LAST, dtype=tf.int32, shape=[batch_size],
          name='step_type'),
      tf.constant(reward, dtype=tf.float32, shape=[batch_size], name='reward'),
      tf.constant(1.0, dtype=tf.float32, shape=[batch_size], name='discount'),
      tf.constant(observation + 100.0, dtype=tf.float32,
                  shape=[batch_size, context_dim], name='observation'))
  return initial_step, final_step


def _get_action_step(action):
  return policy_step.PolicyStep(
      action=tf.convert_to_tensor(action))


def _get_experience(initial_step, action_step, final_step):
  single_experience = driver_utils.trajectory_for_bandit(
      initial_step, action_step, final_step)
  # Adds a 'time' dimension.
  return tf.nest.map_structure(
      lambda x: tf.expand_dims(tf.convert_to_tensor(x), 1),
      single_experience)


@test_util.run_all_in_graph_and_eager_modes
class NeuralLinUCBAgentTest(tf.test.TestCase, parameterized.TestCase):

  def setUp(self):
    super(NeuralLinUCBAgentTest, self).setUp()
    tf.compat.v1.enable_resource_variables()

  @test_cases()
  def testInitializeAgentNumTrainSteps0(self, batch_size, context_dim):
    num_actions = 5
    observation_spec = tensor_spec.TensorSpec([context_dim], tf.float32)
    time_step_spec = time_step.time_step_spec(observation_spec)
    action_spec = tensor_spec.BoundedTensorSpec(
        dtype=tf.int32, shape=(), minimum=0, maximum=num_actions - 1)

    encoder = DummyNet(obs_dim=context_dim)
    agent = neural_linucb_agent.NeuralLinUCBAgent(
        time_step_spec=time_step_spec,
        action_spec=action_spec,
        encoding_network=encoder,
        encoding_network_num_train_steps=0,
        encoding_dim=10,
        optimizer=None)
    self.evaluate(agent.initialize())

  @test_cases()
  def testInitializeAgentNumTrainSteps10(self, batch_size, context_dim):
    num_actions = 5
    observation_spec = tensor_spec.TensorSpec([context_dim], tf.float32)
    time_step_spec = time_step.time_step_spec(observation_spec)
    action_spec = tensor_spec.BoundedTensorSpec(
        dtype=tf.int32, shape=(), minimum=0, maximum=num_actions - 1)

    encoder = DummyNet(obs_dim=context_dim)
    agent = neural_linucb_agent.NeuralLinUCBAgent(
        time_step_spec=time_step_spec,
        action_spec=action_spec,
        encoding_network=encoder,
        encoding_network_num_train_steps=10,
        encoding_dim=10,
        optimizer=None)
    self.evaluate(agent.initialize())

  @test_cases()
  def testNeuralLinUCBUpdateNumTrainSteps0(self, batch_size=1, context_dim=10):
    """Check NeuralLinUCBAgent updates when behaving like LinUCB."""

    # Construct a `Trajectory` for the given action, observation, reward.
    num_actions = 5
    initial_step, final_step = _get_initial_and_final_steps(
        batch_size, context_dim)
    action = np.random.randint(num_actions, size=batch_size, dtype=np.int32)
    action_step = _get_action_step(action)
    experience = _get_experience(initial_step, action_step, final_step)

    # Construct an agent and perform the update.
    observation_spec = tensor_spec.TensorSpec([context_dim], tf.float32)
    time_step_spec = time_step.time_step_spec(observation_spec)
    action_spec = tensor_spec.BoundedTensorSpec(
        dtype=tf.int32, shape=(), minimum=0, maximum=num_actions - 1)
    encoder = DummyNet(obs_dim=context_dim)
    encoding_dim = 10
    agent = neural_linucb_agent.NeuralLinUCBAgent(
        time_step_spec=time_step_spec,
        action_spec=action_spec,
        encoding_network=encoder,
        encoding_network_num_train_steps=0,
        encoding_dim=encoding_dim,
        optimizer=tf.compat.v1.train.AdamOptimizer(learning_rate=1e-2))

    loss_info = agent.train(experience)
    self.evaluate(agent.initialize())
    self.evaluate(tf.compat.v1.global_variables_initializer())
    self.evaluate(loss_info)
    final_a = self.evaluate(agent.cov_matrix)
    final_b = self.evaluate(agent.data_vector)

    # Compute the expected updated estimates.
    observations_list = tf.dynamic_partition(
        data=tf.reshape(tf.cast(experience.observation, tf.float64),
                        [batch_size, context_dim]),
        partitions=tf.convert_to_tensor(action),
        num_partitions=num_actions)
    rewards_list = tf.dynamic_partition(
        data=tf.reshape(tf.cast(experience.reward, tf.float64), [batch_size]),
        partitions=tf.convert_to_tensor(action),
        num_partitions=num_actions)
    expected_a_updated_list = []
    expected_b_updated_list = []
    for _, (observations_for_arm, rewards_for_arm) in enumerate(zip(
        observations_list, rewards_list)):

      encoded_observations_for_arm, _ = encoder(observations_for_arm)
      encoded_observations_for_arm = tf.cast(
          encoded_observations_for_arm, dtype=tf.float64)

      num_samples_for_arm_current = tf.cast(
          tf.shape(rewards_for_arm)[0], tf.float64)
      num_samples_for_arm_total = num_samples_for_arm_current

      # pylint: disable=cell-var-from-loop
      def true_fn():
        a_new = tf.eye(encoding_dim, dtype=tf.float64) + tf.matmul(
            encoded_observations_for_arm, encoded_observations_for_arm,
            transpose_a=True)
        b_new = bandit_utils.sum_reward_weighted_observations(
            rewards_for_arm, encoded_observations_for_arm)
        return a_new, b_new
      def false_fn():
        return (tf.eye(encoding_dim, dtype=tf.float64),
                tf.zeros([encoding_dim], dtype=tf.float64))
      a_new, b_new = tf.cond(
          tf.squeeze(num_samples_for_arm_total) > 0,
          true_fn,
          false_fn)

      expected_a_updated_list.append(self.evaluate(a_new))
      expected_b_updated_list.append(self.evaluate(b_new))

    # Check that the actual updated estimates match the expectations.
    self.assertAllClose(expected_a_updated_list, final_a)
    self.assertAllClose(expected_b_updated_list, final_b)

  @test_cases()
  def testNeuralLinUCBUpdateNumTrainSteps10(self, batch_size=1, context_dim=10):
    """Check NeuralLinUCBAgent updates when behaving like eps-greedy."""

    # Construct a `Trajectory` for the given action, observation, reward.
    num_actions = 5
    initial_step, final_step = _get_initial_and_final_steps(
        batch_size, context_dim)
    action = np.random.randint(num_actions, size=batch_size, dtype=np.int32)
    action_step = _get_action_step(action)
    experience = _get_experience(initial_step, action_step, final_step)

    # Construct an agent and perform the update.
    observation_spec = tensor_spec.TensorSpec([context_dim], tf.float32)
    time_step_spec = time_step.time_step_spec(observation_spec)
    action_spec = tensor_spec.BoundedTensorSpec(
        dtype=tf.int32, shape=(), minimum=0, maximum=num_actions - 1)
    encoder = DummyNet(obs_dim=context_dim)
    encoding_dim = 10
    agent = neural_linucb_agent.NeuralLinUCBAgent(
        time_step_spec=time_step_spec,
        action_spec=action_spec,
        encoding_network=encoder,
        encoding_network_num_train_steps=10,
        encoding_dim=encoding_dim,
        optimizer=tf.compat.v1.train.AdamOptimizer(learning_rate=0.001))

    loss_info_before, _ = agent.train(experience)
    loss_info_after, _ = agent.train(experience)
    self.evaluate(agent.initialize())
    self.evaluate(tf.compat.v1.global_variables_initializer())
    loss_before_value = self.evaluate(loss_info_before)
    loss_after_value = self.evaluate(loss_info_after)
    self.assertLess(
        np.absolute(loss_before_value - loss_after_value), 10.0)


if __name__ == '__main__':
  tf.test.main()