# Code to run a chess match between one-node Leela and gpt-3.5-turbo-instruct.
# Works in February 2025 with python 3.11 and the OpenAI completions endpoint
# which still exists at the time of writing.
#
# You'll need to have the Leela lc0 executable and a network for it, modifying
# the appropriate variables below.
#
# The existence of folders called game_logs and illegal_moves is assumed.  I
# could create them from the script but life is too short.
#
# Openings used are listed one line after the next in a file called
# openings.txt.  A hundred games takes about an hour to run.

import subprocess
import chess
import chess.pgn
from openai import OpenAI
import sys
from datetime import datetime

file_timestamp = f"{datetime.now():%Y-%m-%d_%H%M}"

network_name = "BT4-1740.pb.gz"

lc0_folder = "path/to/lc0"
network = lc0_folder + "/" + network_name
lc0_exe = lc0_folder + "/lc0.exe"

openai_model = "gpt-3.5-turbo-instruct"
base_temperature = 0

output_pgn = f"game_logs/{network_name}_{file_timestamp}_temp{base_temperature}.pgn"

with open("openings.txt", "r") as f:
  openings = f.readlines()

illegal_move_file = f"illegal_moves/illegal_moves_{file_timestamp}.txt"
with open(illegal_move_file, "w") as f:
  pass

with open("openai_api_key.txt", "r") as f:
	api_key = f.readline().replace("\n", "")
client = OpenAI(api_key = api_key)

with open("pgn_headers.txt", "r") as f:
  pgn_header = f.read()


lc0_process = subprocess.Popen(
  [lc0_exe, "--backend=blas", "-w", network],
  stdin = subprocess.PIPE,
  stdout = subprocess.PIPE,
  text = True,
  bufsize = 1  # line-buffered
)

def send_cmd(cmd):
  lc0_process.stdin.write(cmd + "\n")
  lc0_process.stdin.flush()

def read_until(keyword):
  while True:
    line = lc0_process.stdout.readline()
    if not line:
      # Engine closed unexpectedly
      raise RuntimeError("lc0 closed or no output.")
    if keyword in line:
      break

send_cmd("uci")
read_until("uciok")
send_cmd("isready")
read_until("readyok")


def get_leela_material_advantage():
  fen = board.fen().split(" ")[0]
  w = 0
  b = 0
  for character in fen:
    if character == "P":
      w += 1
    elif character == "p":
      b += 1
    elif character == "R":
      w += 5
    elif character == "r":
      b += 5
    elif character == "N":
      w += 3
    elif character == "n":
      b += 3
    elif character == "B":
      w += 3
    elif character == "b":
      b += 3
    elif character == "Q":
      w += 9
    elif character == "q":
      b += 9

  sign = 1 if leela_color == chess.WHITE else -1
  return sign * (w - b)

def get_pgn_string_exporter(cols, headers):
  return chess.pgn.StringExporter(
    columns = cols,
    headers = headers,
    variations = False,
    comments = False)

def write_game(result):
  game = chess.pgn.Game.from_board(board)
  if leela_color == chess.WHITE:
    game.headers["White"] = "Leela"
    game.headers["Black"] = "GPT"
  else:
    game.headers["White"] = "GPT"
    game.headers["Black"] = "Leela"
  game.headers["Result"] = result
  game.headers["Date"] = f"{datetime.now():%Y.%m.%d}"

  exporter = get_pgn_string_exporter(80, True)
  pgn = game.accept(exporter)

  with open(output_pgn, "a") as f:
    f.write(pgn + "\n\n")
  
  global leela_wins
  global draws
  global gpt_wins

  if (result == "1-0" and leela_color == chess.WHITE) or (result == "0-1" and leela_color == chess.BLACK):
    leela_wins += 1
  elif result == "1/2-1/2":
    draws += 1
  else:
    gpt_wins += 1
  
  print(f"\nLeela {leela_wins} Draw {draws} GPT {gpt_wins}")


leela_winning_threshold = 5
leela_winning_adjudicate_count = 10

game_counter = 0

leela_wins = 0
draws = 0
gpt_wins = 0


for opening_line in openings:
  game_counter += 1
  opening = opening_line.strip()
  print(f"Game {game_counter}: {opening}")
  # The openings.txt file has spaces after the dots.
  opening_moves_san = [m for m in opening.split(" ") if not "." in m]
  opening_moves_uci = []
  
  # Set up a temporary board to convert SAN to UCI
  board = chess.Board()
  for move_san in opening_moves_san:
    move_obj = board.push_san(move_san)
    opening_moves_uci.append(move_obj.uci())

  for leela_color in [chess.WHITE, chess.BLACK]:
    leela_winning_advantage_count = 0

    board = chess.Board()
    moves = []
    for move in opening_moves_uci:
      moves.append(move)
      board.push_uci(move)
    
    print()
    
    while True:
      sys.stdout.write(f"\rMove {board.fullmove_number} {'W' if board.turn == chess.WHITE else 'B'}")
      sys.stdout.flush()
      if board.turn == leela_color:
        send_cmd(f"position startpos moves {' '.join(moves)}")
        send_cmd("go nodes 1")

        # Read lines until we see "bestmove"
        while True:
          line = lc0_process.stdout.readline()
          if not line:
            raise Exception("Error")
          if line.startswith("bestmove"):
            parts = line.split()
            if len(parts) >= 2:
              move = parts[1]
              break
      
        moves.append(move)
        board.push_uci(move)
      else:
        # GPT move
        game = chess.pgn.Game.from_board(board)
        # The exporter must be re-defined, otherwise the output string contains
        # all the previous PGN's!!!
        exporter = get_pgn_string_exporter(None, False)

        pgn = game.accept(exporter).replace(" *", "")
        # Better results with "1.e4" than "1. e4":
        pgn = pgn.replace(". ", ".")
        n = board.ply()
        move_number = board.fullmove_number
        prefix = " " if move_number > 1 else ""
        append_text = f"{prefix}{move_number}." if leela_color == chess.BLACK else ""
        

        illegal_moves = 0
        terminate_game = False

        # Try a few times to get a legal move in the completion:
        temperature = base_temperature
        while True:
          completion = client.completions.create(
            model = openai_model,
            prompt = pgn_header + pgn + append_text,
            temperature = temperature,
            max_tokens = 5)
          
          # Allow for some variation in the completions if the first attempt does not
          # give a valid move:
          temperature = 1

          completion_text = completion.choices[0].text.strip()
          compare_text = completion_text
          completion_text = completion_text.encode("ascii", "ignore").decode("ascii")

          if completion_text != compare_text:
            # In case there are weird characters
            with open(illegal_move_file, "a") as f:
              f.write(pgn + append_text + "|" + "<NON-ASCII>\n\n")

          if completion_text.startswith("."):
            # Occasional systematic PGN completion error that may neverthless contain
            # a good chess move.
            with open(illegal_move_file, "a") as f:
              f.write(pgn + append_text + "|" + completion_text + "\n\n")
          
          while completion_text.startswith("."):
            completion_text = completion_text[1:]
          move_san = "<EMPTY>" if completion_text == "" else completion_text.split()[0]

          try:
            move_obj = board.push_san(move_san)
            move = move_obj.uci()
            moves.append(move)
            break
          except:
            illegal_moves += 1
            with open(illegal_move_file, "a") as f:
              f.write(pgn + append_text + "|" + completion_text + "\n\n")
            if illegal_moves >= 5:
              terminate_game = True
              break
        
        if terminate_game:
          write_game("1-0" if leela_color == chess.WHITE else "0-1")
          break


      result = board.outcome(claim_draw = True)
      if not result is None:
        write_game(result.result())
        break

      if get_leela_material_advantage() >= leela_winning_threshold:
        leela_winning_advantage_count += 1
      else:
        leela_winning_advantage_count = 0
      
      if leela_winning_advantage_count >= leela_winning_adjudicate_count:
        write_game("1-0" if leela_color == chess.WHITE else "0-1")
        break
