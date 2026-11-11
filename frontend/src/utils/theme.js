// src/utils/theme.js
// Palettes JS du thème. Elles reprennent exactement les valeurs CSS de
// src/styles/index.css (variables --ink, --accent…), pour les endroits où une
// variable CSS ne marche pas : attributs SVG des graphiques (recharts) et
// couleurs concaténées avec une opacité (`${couleur}22`).
//
// Les clés historiques sont conservées : `brand.purple` désigne désormais la
// couleur d'accent (bleu acier), `brand.green/amber/red` les états.

export const themes = {
  light: {
    name: 'light',
    bg: {
      primary:   '#F6F7F8',
      secondary: '#FFFFFF',
      tertiary:  '#EEF1F4',
      card:      '#FFFFFF',
      input:     '#FFFFFF',
      hover:     'rgba(23,33,43,.04)',
    },
    text: {
      primary:   '#17212B',
      secondary: '#4B5867',
      muted:     '#77828F',
      code:      '#17212B',
    },
    border: {
      primary:   '#D8DDE3',
      secondary: '#E6EAEE',
      focus:     '#1F5FA6',
    },
    brand: {
      purple: '#1F5FA6',   // accent
      teal:   '#2F7F7A',   // 2ᵉ série de données
      red:    '#B42318',   // critique
      amber:  '#9A6312',   // attention
      green:  '#2E7D4F',   // sain
      blue:   '#1F5FA6',
    },
    sidebar: {
      bg:     '#FFFFFF',
      border: '#D8DDE3',
      active: 'rgba(31,95,166,.09)',
    },
    scrollbar: {
      track:  'transparent',
      thumb:  '#BCC4CD',
    }
  },

  dark: {
    name: 'dark',
    bg: {
      primary:   '#12161B',
      secondary: '#181E25',
      tertiary:  '#1F262E',
      card:      '#181E25',
      input:     '#181E25',
      hover:     'rgba(228,232,236,.04)',
    },
    text: {
      primary:   '#E4E8EC',
      secondary: '#9AA5B1',
      muted:     '#6F7A86',
      code:      '#E4E8EC',
    },
    border: {
      primary:   '#2A323C',
      secondary: '#222932',
      focus:     '#72A9E6',
    },
    brand: {
      purple: '#72A9E6',
      teal:   '#5FB3AC',
      red:    '#F07167',
      amber:  '#D9A548',
      green:  '#5CB585',
      blue:   '#72A9E6',
    },
    sidebar: {
      bg:     '#181E25',
      border: '#2A323C',
      active: 'rgba(114,169,230,.12)',
    },
    scrollbar: {
      track:  'transparent',
      thumb:  '#3A444F',
    }
  }
}

export function getTheme(name) {
  return themes[name] || themes.light
}

export function getSavedTheme() {
  try {
    return localStorage.getItem('aikube-theme') || 'light'
  } catch {
    return 'light'
  }
}

export function saveTheme(name) {
  try {
    localStorage.setItem('aikube-theme', name)
  } catch {}
}
