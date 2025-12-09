import random

class TetrisGame:
    def __init__(self):
        # Spec 1: 가로 10, 세로 20 그리드 구현
        self.width = 10
        self.height = 20
        self.board = [[0 for _ in range(self.width)] for _ in range(self.height)]
        self.score = 0
        self.game_over = False

    def new_piece(self):
        """새로운 블록 생성"""
        shapes = ['I', 'J', 'L', 'O', 'S', 'T', 'Z']
        return random.choice(shapes)

    def check_lines(self):
        # Spec 2: 꽉 찬 줄 삭제 및 점수 로직
        lines_to_clear = []
        for y, row in enumerate(self.board):
            if all(cell != 0 for cell in row):
                lines_to_clear.append(y)
        
        if lines_to_clear:
            # 줄 삭제
            for y in lines_to_clear:
                del self.board[y]
                self.board.insert(0, [0] * self.width)
            # 점수 증가
            self.score += len(lines_to_clear) * 100
            print(f"Lines cleared! Current Score: {self.score}")

    def check_game_over(self):
        # Spec 3: 천장(0번째 줄)에 블록이 있으면 게임 오버
        if any(cell != 0 for cell in self.board[0]):
            self.game_over = True
            print("Game Over!")
            return True
        return Falsei