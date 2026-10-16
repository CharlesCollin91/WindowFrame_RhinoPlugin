# WindowFrame_CC

**Français** · [English](README.md)

Un script Python pour Rhino 8 qui construit un **cadre de fenêtre** à section rectangulaire le long du contour extérieur de toute géométrie sélectionnée (courbes, surfaces, polysurfaces, maillages, SubD, hachures). Il ouvre une petite fenêtre flottante : sélectionne la géométrie, règle les largeurs, appuie sur **Run**.

Version **1.11** · Licence : [GPL-3.0 ou ultérieure](LICENSE) · Visite du code : [ARCHITECTURE.md](ARCHITECTURE.md) (en anglais)

## Prérequis
- Windows, Rhino 8 ou plus récent (le Python 3 intégré est utilisé)
- Aucun paquet supplémentaire

## Installation
1. Télécharge `WindowFrame_CC.py` (bouton vert **Code** > **Download ZIP**, ou le fichier seul) et place-le dans le dossier de ton choix, par exemple `C:\Outils\WindowFrame_CC\`.
2. Dans Rhino, crée un bouton de barre d'outils avec cette macro (adapte le chemin) :
   ```
   ! _-RunPythonScript "C:\Outils\WindowFrame_CC\WindowFrame_CC.py"
   ```
   Ou lance-le une fois depuis l'éditeur de script de Rhino (commande `ScriptEditor` > ouvrir le fichier > Run).
3. Clique le bouton : la fenêtre WindowFrame s'ouvre. Fermer puis rouvrir la fenêtre suffit pour charger une nouvelle version du fichier.

Le plugin écrit ses journaux de session dans un dossier `logs` à côté du script. Supprime le dossier pour désinstaller.

## Utilisation
1. Sélectionne la géométrie dont le contour doit devenir un cadre.
2. Règle les largeurs, le décalage, la profondeur et la sortie (tableau ci-dessous).
3. Appuie sur **Run**. Un seul Ctrl+Z annule tout le Run. Tes objets d'entrée ne sont jamais modifiés.

## Entrées
Courbes (ouvertes ou fermées, un ou plusieurs segments), surfaces, polysurfaces, extrusions, maillages, SubD, hachures. Seul le bord extérieur est utilisé ; les trous sont ignorés. Un cadre est créé par contour.
- Les courbes qui se touchent, sélectionnées ensemble, sont toujours jointes d'abord (nécessaire pour des jonctions propres).
- `Close open outlines` (activé par défaut) : un contour ouvert est fermé (petit écart : recollé ; sinon un segment droit). Désactivé : le contour reçoit un cadre ouvert, bouts coupés droit.
- L'entrée est nettoyée d'abord : les morceaux colinéaires ou cocirculaires sont fusionnés. S'il reste plus de 8 segments (arêtes de maillage, polylignes explosées), chaque tronçon entre de vrais coins (35 degrés ou plus) est reconstruit en une ligne ou une courbe lisse.

## Réglages (mm, convertis dans les unités du document)
| Réglage | Effet |
|---|---|
| Sides / Top / Bottom | Largeur d'un membre, dans le plan du contour. `Link` = une seule valeur pour les trois. |
| Offset : Inside / Outside / Center | Le contour est la face extérieure / la face intérieure / le milieu du cadre. |
| Depth + Front / Back / Center | Épaisseur perpendiculaire au plan du contour. Front = vers la caméra de la vue active (-Y, +X ou +Z du monde si la caméra est de profil). |
| Output : Solid / Mitered 45 / Mitered 90 | **Solid** : une seule pièce. **Mitered 45** : une pièce par côté, joints en diagonale. **Mitered 90** : une pièce par côté, joints droits (le haut et le bas courent sur toute la largeur, les côtés s'arrêtent entre eux). |
| Ungroup / Group / Group all | Aucun groupe ; un groupe par cadre ; un groupe par cadre plus un groupe extérieur autour de tous les cadres du Run. |

Reconnaissance côté / haut / bas : le « haut » est le Z du monde projeté dans le plan du contour (le Y du monde pour un contour à plat). Un segment à moins de 45 degrés du haut est un côté ; sinon c'est un haut s'il est au-dessus du centre du contour, un bas s'il est en dessous.

## Test intégré
`Test set-1` construit 20 entrées de référence sur un calque nommé `WindowFrame_CC_TEST`, loin du modèle, et lance 27 combinaisons (Offset x Output x Depth) sur chacune : 540 essais. Les résultats sont disposés en grille colorée vert / ambre / rouge (PASS / WARN / FAIL). Vérifications : solide valide, fermé, bon nombre de membres, nombre de faces, volume plausible. Résultats complets dans `logs\TEST_<date>.json`. Un seul Ctrl+Z annule le test.

## Limites connues
- Section rectangulaire seulement (pas encore de profil de moulure personnalisé).
- Les sorties Mitered sont construites membre par membre (pas de découpe booléenne). Sur un contour non planaire, la profondeur suit le plan moyen ; un contour tordu laisse une petite marche aux coins.
- Solid sur un contour non planaire = une peau continue ; si elle échoue, l'union des pièces à 45 degrés est utilisée (le rapport l'indique).
- Joints bout à bout : quand deux membres de même type se rencontrent, le plus long passe.
- Un contour fermé sans coin (cercle, ellipse) est construit en deux moitiés coupées droit dans les sorties Mitered.
- Plusieurs contours fermés en mode Mitered sont moins fiables pour l'instant.
- Les solides fermés (sans arête libre) et les maillages fermés sont refusés.
- Une ligne droite seule n'a pas d'intérieur : Center est utilisé.
- Les contours de maillage sont des polylignes : chaque petit segment est classé seul, donc des largeurs haut / côté différentes peuvent alterner sur les bords courbes d'un maillage.
- Coins très aigus (moins d'environ 6 degrés) : pas de coupe d'onglet, un pont droit est utilisé.
- Une largeur plus grande qu'un segment court ou qu'un arc serré : ce contour est refusé.

## Contribuer
Les améliorations sont bienvenues. Commence par [ARCHITECTURE.md](ARCHITECTURE.md), qui explique comment le fichier unique est organisé et où se trouve chaque étape. Ouvre une « issue » pour décrire un bogue (joins le fichier correspondant de `logs\`) ou une idée avant un gros changement. L'historique des versions est dans [CHANGELOG.md](CHANGELOG.md).

## Licence
GNU General Public License v3.0 ou ultérieure. Tu peux utiliser, étudier, modifier et partager ce code ; toute version modifiée et distribuée doit rester sous la même licence. Voir [LICENSE](LICENSE).
