#!/usr/bin/env python3
"""Q-learning trainer for Pong AI opponent.

Self-contained — includes a minimal PongGame so it runs standalone.
Outputs a Q-table JSON that pong_ai.py loads at runtime.

Usage:
    python pong_train.py --episodes 50000 --output ~/models/pong_qtable.json
"""

import argparse
import json
import math
import os
import random
import sys
import time


# ---------------------------------------------------------------------------
# Minimal PongGame (mirrors cortex-core/src/games/pong.py)
# ---------------------------------------------------------------------------
class PongGame:
    WIDTH = 240
    HEIGHT = 280
    PADDLE_W = 6
    PADDLE_H = 40
    PADDLE_MARGIN = 8
    PADDLE_SPEED = 200
    BALL_SIZE = 6
    BALL_SPEED_INITIAL = 120
    BALL_SPEED_MAX = 220
    BALL_SPEED_INCREMENT = 8
    WIN_SCORE = 5

    def __init__(self):
        self.paddle1_y = 0.0
        self.paddle2_y = 0.0
        self.ball_x = 0.0
        self.ball_y = 0.0
        self.ball_vx = 0.0
        self.ball_vy = 0.0
        self.score1 = 0
        self.score2 = 0
        self.game_over = False
        self.winner = None
        self._ball_speed = self.BALL_SPEED_INITIAL
        self._serve_pause = 0.0
        self.reset()

    def reset(self):
        self.paddle1_y = self.HEIGHT / 2
        self.paddle2_y = self.HEIGHT / 2
        self.score1 = 0
        self.score2 = 0
        self.game_over = False
        self.winner = None
        self._reset_ball(direction=1)

    def _reset_ball(self, direction=1):
        self.ball_x = self.WIDTH / 2
        self.ball_y = self.HEIGHT / 2
        angle = random.uniform(-0.5, 0.5)
        self.ball_vx = self.BALL_SPEED_INITIAL * math.cos(angle) * direction
        self.ball_vy = self.BALL_SPEED_INITIAL * math.sin(angle)
        self._ball_speed = self.BALL_SPEED_INITIAL
        self._serve_pause = 0.0  # no pause during training

    def tick(self, dt):
        if self.game_over:
            return None
        self.ball_x += self.ball_vx * dt
        self.ball_y += self.ball_vy * dt
        half = self.BALL_SIZE / 2

        if self.ball_y - half <= 0:
            self.ball_y = half
            self.ball_vy = abs(self.ball_vy)
        elif self.ball_y + half >= self.HEIGHT:
            self.ball_y = self.HEIGHT - half
            self.ball_vy = -abs(self.ball_vy)

        p1_x = self.PADDLE_MARGIN + self.PADDLE_W
        p1_top = self.paddle1_y - self.PADDLE_H / 2
        p1_bot = self.paddle1_y + self.PADDLE_H / 2
        if (self.ball_x - half <= p1_x and self.ball_vx < 0
                and p1_top <= self.ball_y <= p1_bot):
            self.ball_x = p1_x + half
            self._bounce_off_paddle(self.paddle1_y)

        p2_x = self.WIDTH - self.PADDLE_MARGIN - self.PADDLE_W
        p2_top = self.paddle2_y - self.PADDLE_H / 2
        p2_bot = self.paddle2_y + self.PADDLE_H / 2
        if (self.ball_x + half >= p2_x and self.ball_vx > 0
                and p2_top <= self.ball_y <= p2_bot):
            self.ball_x = p2_x - half
            self._bounce_off_paddle(self.paddle2_y)

        event = None
        if self.ball_x < 0:
            self.score2 += 1
            event = {"scored": 2}
            if self.score2 >= self.WIN_SCORE:
                self.game_over = True
                self.winner = 2
            else:
                self._reset_ball(direction=1)
        elif self.ball_x > self.WIDTH:
            self.score1 += 1
            event = {"scored": 1}
            if self.score1 >= self.WIN_SCORE:
                self.game_over = True
                self.winner = 1
            else:
                self._reset_ball(direction=-1)
        return event

    def _bounce_off_paddle(self, paddle_y):
        offset = (self.ball_y - paddle_y) / (self.PADDLE_H / 2)
        offset = max(-1.0, min(1.0, offset))
        self._ball_speed = min(self._ball_speed + self.BALL_SPEED_INCREMENT,
                               self.BALL_SPEED_MAX)
        angle = offset * math.pi / 4
        direction = 1 if self.ball_vx < 0 else -1
        self.ball_vx = self._ball_speed * math.cos(angle) * direction
        self.ball_vy = self._ball_speed * math.sin(angle)

    def move_paddle(self, player, direction, dt):
        delta = direction * self.PADDLE_SPEED * dt
        half_h = self.PADDLE_H / 2
        if player == 1:
            self.paddle1_y = max(half_h, min(self.HEIGHT - half_h,
                                             self.paddle1_y + delta))
        else:
            self.paddle2_y = max(half_h, min(self.HEIGHT - half_h,
                                             self.paddle2_y + delta))

    def get_state(self):
        return {
            "ball_x": self.ball_x, "ball_y": self.ball_y,
            "ball_vx": self.ball_vx, "ball_vy": self.ball_vy,
            "paddle1_y": self.paddle1_y, "paddle2_y": self.paddle2_y,
            "score1": self.score1, "score2": self.score2,
            "game_over": self.game_over, "winner": self.winner,
        }

    def get_ai_observation(self):
        bx = max(0, min(11, int(self.ball_x / self.WIDTH * 12)))
        by = max(0, min(9, int(self.ball_y / self.HEIGHT * 10)))
        vy_sign = 0 if self.ball_vy < -20 else (2 if self.ball_vy > 20 else 1)
        py = max(0, min(9, int(self.paddle2_y / self.HEIGHT * 10)))
        return (bx, by, vy_sign, py)


# ---------------------------------------------------------------------------
# Rule-based opponent for player 1 (during training)
# ---------------------------------------------------------------------------
def rule_based_p1(state, difficulty=0.85):
    """Simple tracking AI for player 1."""
    if state["ball_vx"] >= 0:
        return 0
    diff = state["ball_y"] - state["paddle1_y"]
    if abs(diff) < 10:
        return 0
    if random.random() > difficulty:
        return 0
    return 1 if diff > 0 else -1


# ---------------------------------------------------------------------------
# Q-learning trainer
# ---------------------------------------------------------------------------
class QLearningTrainer:
    def __init__(self, alpha=0.1, gamma=0.95, epsilon_start=1.0,
                 epsilon_end=0.05, epsilon_decay_episodes=30000):
        self.alpha = alpha
        self.gamma = gamma
        self.epsilon = epsilon_start
        self.epsilon_end = epsilon_end
        self.epsilon_decay = (epsilon_start - epsilon_end) / epsilon_decay_episodes
        self.q_table = {}  # state_tuple -> [q_up, q_stay, q_down]
        self.actions = [-1, 0, 1]  # up, stay, down

    def _get_q(self, state):
        if state not in self.q_table:
            self.q_table[state] = [0.0, 0.0, 0.0]
        return self.q_table[state]

    def choose_action(self, state):
        if random.random() < self.epsilon:
            return random.choice(self.actions)
        q = self._get_q(state)
        best = max(q)
        # Random tiebreak
        candidates = [i for i, v in enumerate(q) if v == best]
        return self.actions[random.choice(candidates)]

    def update(self, state, action, reward, next_state):
        q = self._get_q(state)
        next_q = self._get_q(next_state)
        action_idx = self.actions.index(action)
        q[action_idx] += self.alpha * (
            reward + self.gamma * max(next_q) - q[action_idx]
        )

    def decay_epsilon(self):
        self.epsilon = max(self.epsilon_end, self.epsilon - self.epsilon_decay)

    def save(self, path):
        table = {}
        for key, values in self.q_table.items():
            key_str = ",".join(str(k) for k in key)
            table[key_str] = values
        data = {
            "type": "qtable",
            "states": len(self.q_table),
            "table": table,
        }
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f)


def train(episodes, output_path, sim_fps=30, log_interval=1000):
    """Run Q-learning training."""
    trainer = QLearningTrainer()
    game = PongGame()
    dt = 1.0 / sim_fps

    total_wins = 0
    start_time = time.time()
    last_log = start_time

    print(f"Training Pong AI for {episodes} episodes...")
    print(f"Output: {output_path}")
    print(f"Simulation FPS: {sim_fps}")
    print()

    for ep in range(1, episodes + 1):
        game.reset()
        steps = 0
        episode_reward = 0.0

        while not game.game_over and steps < 5000:
            steps += 1
            state = game.get_ai_observation()

            # Q-learner controls paddle 2 (right)
            action = trainer.choose_action(state)
            game.move_paddle(2, action, dt)

            # Rule-based player 1 (left)
            p1_action = rule_based_p1(game.get_state())
            game.move_paddle(1, p1_action, dt)

            # Physics tick
            event = game.tick(dt)

            # Compute reward
            reward = 0.0
            if event:
                if event["scored"] == 2:
                    reward = 1.0   # AI scored
                elif event["scored"] == 1:
                    reward = -1.0  # opponent scored

            # Small tracking reward: closer paddle to ball = small positive
            if game.ball_vx > 0:  # ball heading toward AI
                dist = abs(game.ball_y - game.paddle2_y) / game.HEIGHT
                reward += 0.01 * (1.0 - dist)

            next_state = game.get_ai_observation()
            trainer.update(state, action, reward, next_state)
            episode_reward += reward

        if game.winner == 2:
            total_wins += 1

        trainer.decay_epsilon()

        # Progress logging
        if ep % log_interval == 0 or ep == 1:
            elapsed = time.time() - start_time
            eps_per_sec = ep / elapsed if elapsed > 0 else 0
            remaining = (episodes - ep) / eps_per_sec if eps_per_sec > 0 else 0
            win_rate = total_wins / ep * 100

            # Format ETA
            if remaining < 60:
                eta_str = f"{remaining:.0f}s"
            elif remaining < 3600:
                eta_str = f"{remaining/60:.1f}m"
            else:
                eta_str = f"{remaining/3600:.1f}h"

            print(
                f"Episode {ep}/{episodes} | "
                f"States: {len(trainer.q_table)}/3600 | "
                f"Epsilon: {trainer.epsilon:.4f} | "
                f"Win%: {win_rate:.1f} | "
                f"Speed: {eps_per_sec:.0f} ep/s | "
                f"ETA: {eta_str}"
            )
            sys.stdout.flush()

    # Save
    trainer.save(output_path)
    elapsed = time.time() - start_time
    file_size = os.path.getsize(output_path)
    print()
    print(f"Training complete!")
    print(f"  Episodes: {episodes}")
    print(f"  Q-table states: {len(trainer.q_table)}/3600")
    print(f"  Final win rate: {total_wins/episodes*100:.1f}%")
    print(f"  Time: {elapsed:.1f}s ({elapsed/60:.1f}m)")
    print(f"  Output: {output_path} ({file_size/1024:.0f} KB)")


def main():
    parser = argparse.ArgumentParser(description="Train Pong AI via Q-learning")
    parser.add_argument("--episodes", type=int, default=50000,
                        help="Number of training episodes (default: 50000)")
    parser.add_argument("--output", type=str,
                        default=os.path.expanduser("~/models/pong_qtable.json"),
                        help="Output Q-table JSON path")
    parser.add_argument("--fps", type=int, default=30,
                        help="Simulation FPS for physics (default: 30)")
    parser.add_argument("--log-interval", type=int, default=1000,
                        help="Log progress every N episodes (default: 1000)")
    args = parser.parse_args()

    train(args.episodes, args.output, sim_fps=args.fps,
          log_interval=args.log_interval)


if __name__ == "__main__":
    main()
