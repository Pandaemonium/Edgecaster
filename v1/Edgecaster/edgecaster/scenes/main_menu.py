from __future__ import annotations

from typing import List

import pygame

from .base import Scene
from .character_creation_scene import CharacterCreationScene
from .saved_games_scene import SavedGamesScene
from .options_scene import OptionsScene


class MainMenuScene(Scene):
    """Top-level main menu that can launch other scenes."""

    def __init__(self) -> None:
        # Remember where the cursor is between visits
        self.selected_idx = 0

    def run(self, manager: "SceneManager") -> None:  # type: ignore[name-defined]
        renderer = manager.renderer
        surface = renderer.surface
        clock = pygame.time.Clock()

        options: List[str] = [
            "New Game",
            "Load Game",
            "Options",
            "Quit",
        ]

        running = True
        while running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    # Clear stack / quit game
                    manager.set_scene(None)
                    return

                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        # Esc from main menu exits the game entirely.
                        manager.set_scene(None)
                        return

                    if event.key in (pygame.K_UP, pygame.K_w):
                        self.selected_idx = (self.selected_idx - 1) % len(options)
                    elif event.key in (pygame.K_DOWN, pygame.K_s):
                        self.selected_idx = (self.selected_idx + 1) % len(options)

                    elif event.key in (pygame.K_RETURN, pygame.K_SPACE):
                        choice = options[self.selected_idx]
                        if choice == "New Game":
                            # Hard switch into character creation
                            manager.set_scene(CharacterCreationScene())
                            return
                        elif choice == "Load Game":
                            # Treat Saved Games as a sub-scene: overlay on stack
                            manager.push_scene(SavedGamesScene())
                            return
                        elif choice == "Options":
                            # Treat Options as a sub-scene: overlay on stack
                            manager.push_scene(OptionsScene())
                            return
                        elif choice == "Quit":
                            manager.set_scene(None)
                            return

            # -------- DRAW --------
            surface.fill(renderer.bg)

            # ASCII art banner
            ascii_art = r"""
                                               /\                                
                                              /  \                               
                                             / /\ \                              
                                      /\    / /  \ \    /\                        
                                     /  \  / /    \ \  /  \                       
                                    / /\ \/ /      \ \/ /\ \                      
                           /\      / /  \  /        \  /  \ \      /\             
                          /  \    / /    \/  /\  /\  \/    \ \    /  \            
                         / /\ \  / /   /\   /  \/  \   /\   \ \  / /\ \           
                /\      / /  \ \/ /   /  \ /        \ /  \   \ \/ /  \ \      /\  
               /  \    / /    \  /   /    \    /\    /    \   \  /    \ \    /  \ 
              / /\ \  / /   /\ \/   / /\   \  /  \  /   /\ \   \/ /\   \ \  / /\ \
             / /  \ \/ /   /  \ \  / /  \   \/ /\ \/   /  \ \  / /  \   \ \/ /  \ \
            / /    \  /   /    \ \/ /    \    /  \    /    \ \/ /    \   \  /    \ \
            ====================== E  D  G  E  C  A  S  T  E  R =====================          
            \ \    /\/\   \    / /\ \    /\    / /\    / /\    / /\/\    / /       
             \ \  / /\ \   \  / /  \ \  /  \  / /\ \  / /  \   / / /\ \  / /        
              \ \/ /  \ \   \/ /    \ \/ /\ \/ /  \ \/ /    \ \/ /  \ \/ /         
               \  /    \ \   \      /  /  \  \      / /      \  /    \  /          
                \/      \ \   \    /  /    \  \    / /        \/      \/           
                         \ \   \  /  /      \  \  / /                                
                          \ \   \/  / /\  /\ \  \/ /                                 
                           \ \      /  \/  \ \     /                                 
                            \ \    /        \ \   /                                  
                             \ \  / /\    /\ \ \ /                                   
                              \ \/ /  \  /  \ \/ /                                    
                               \  / /\ \/ /\ \  /                                    
                                \/ /  \  /  \ \/                                     
                                  / /\ \/ /\ \                                        
                                 / /  \  /  \ \                                       
                                /_/____\/____\_\                                      
            """

            y = 80
            for line in ascii_art.splitlines():
                if not line.strip():
                    y += renderer.font.get_height()
                    continue
                surf = renderer.font.render(line, True, renderer.fg)
                surface.blit(surf, ((renderer.width - surf.get_width()) // 2, y))
                y += renderer.font.get_height()

            # Menu options
            y -= 340
            for idx, opt in enumerate(options):
                selected = (idx == self.selected_idx)
                color = renderer.player_color if selected else renderer.fg
                prefix = "▶ " if selected else "  "
                text = renderer.font.render(prefix + opt, True, color)
                surface.blit(text, (renderer.width // 2 - 110, y))
                y += text.get_height() + 10

            # Footer hint
            hint = renderer.small_font.render(
                "↑/↓ or W/S to move, Enter/Space to select, Esc to quit",
                True,
                renderer.dim,
            )
            surface.blit(
                hint,
                ((renderer.width - hint.get_width()) // 2, renderer.height - 40),
            )

            renderer.present()
            clock.tick(60)
