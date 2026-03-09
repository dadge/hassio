# Binance Grid Bot Dashboard - Home Assistant Add-on

Dashboard pour suivre les profits de vos Grid Bots Binance en temps réel.

## Fonctionnalités

- 📊 Affichage des taux de change BTC, ETH, BNB, USDT en EUR
- 📋 Copier/coller les données depuis la page Binance Grid Bot
- 💰 Calcul automatique des profits en EUR et USDC
- ⚙️ Configuration des profits des bots fermés
- 🔄 Actualisation automatique des taux de change
- 📈 Historique avec graphique d'évolution des profits
- 🔒 Protection par mot de passe (optionnel)

## Installation

### Via GitHub Repository

1. Dans Home Assistant, allez dans **Paramètres > Modules complémentaires > Boutique**
2. Cliquez sur **⋮** > **Dépôts**
3. Ajoutez : `https://github.com/dadge/hassio`
4. Installez l'add-on "Binance Grid Bot Dashboard"

### Via Add-on Local

1. Copiez ce dossier dans `/addons/binance-bot-dashboard` sur votre Home Assistant
2. Allez dans **Paramètres > Modules complémentaires > Boutique > ⋮ > Vérifier les mises à jour**
3. L'add-on apparaîtra dans "Local add-ons"

## Configuration

| Option               | Description                         | Valeurs          | Défaut |
| -------------------- | ----------------------------------- | ---------------- | ------ |
| `mode`               | Mode de fonctionnement              | `live` ou `mock` | `live` |
| `password`           | Mot de passe pour protéger l'accès  | texte ou vide    | vide   |
| `binance_api_key`    | Clé API Binance (réservé futur)     | texte            | vide   |
| `binance_secret_key` | Clé secrète Binance (réservé futur) | texte            | vide   |

### Mode Live vs Mock

- **`live`** : Appelle l'API Binance pour obtenir les vrais taux de change
- **`mock`** : Retourne des données de test (utile pour tester)

## Accès

- **Port direct** : http://homeassistant.local:4201
- **Ingress** : Activez "Afficher dans la barre latérale" pour un accès intégré

## Support

Pour signaler un bug ou demander une fonctionnalité : https://github.com/dadge/binance-bot-app/issues
