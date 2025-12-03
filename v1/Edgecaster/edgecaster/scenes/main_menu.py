from __future__ import annotations

from typing import List

from .base import MenuScene
from .character_creation_scene import CharacterCreationScene
from .saved_games_scene import SavedGamesScene
from .options_scene import OptionsScene


ASCII_BANNER = r"""
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


class MainMenuScene(MenuScene):
    """Top-level main menu that can launch other scenes."""

    def get_menu_items(self) -> List[str]:
        return ["New Game", "Load Game", "Options", "Quit"]

    def get_ascii_art(self) -> str:
        return ASCII_BANNER

    def on_back(self, manager: "SceneManager") -> bool:  # type: ignore[name-defined]
        """Esc from main menu: quit the game."""
        manager.set_scene(None)
        return True

    def on_activate(self, index: int, manager: "SceneManager") -> bool:  # type: ignore[name-defined]
        """Handle selecting a menu option. Always close after selection."""
        choice = self.get_menu_items()[index]

        if choice == "New Game":
            manager.set_scene(CharacterCreationScene())

        elif choice == "Load Game":
            # Overlay a save-game browser, then come back to main menu
            manager.push_scene(SavedGamesScene())

        elif choice == "Options":
            # Overlay options menu
            manager.push_scene(OptionsScene())

        elif choice == "Quit":
            manager.set_scene(None)

        return True
