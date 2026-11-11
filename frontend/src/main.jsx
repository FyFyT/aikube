// src/main.jsx
import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import App from './App'
import { ThemeProvider } from './utils/ThemeContext'
import './styles/index.css'

// -----------------------------------------------------------------------------
// Configuration React Query
//
// Avant : staleTime valait 0 (défaut). Toute donnée était considérée périmée
// dès sa réception, donc chaque changement de page relançait un fetch complet
// et les compteurs repartaient à vide le temps de l'aller-retour réseau.
//
//   staleTime 15s          les données restent valides 15s : revenir sur le
//                          dashboard réaffiche instantanément depuis le cache
//   gcTime 10min           le cache survit largement à une navigation
//   refetchOnWindowFocus   désactivé : re-fetcher à chaque retour d'onglet
//                          n'apporte rien ici et fait clignoter les chiffres
//   refetchInterval 30s    rafraîchissement de fond conservé
//
// Le rafraîchissement continue en arrière-plan ; seule la latence perçue
// disparaît, car l'ancienne valeur reste affichée pendant la mise à jour.
// -----------------------------------------------------------------------------

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 15000,
      gcTime: 10 * 60 * 1000,
      refetchInterval: 30000,
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
})

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <ThemeProvider>
          <App />
        </ThemeProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>
)
