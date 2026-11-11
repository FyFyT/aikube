// src/utils/ThemeContext.jsx
// Le thème est porté par l'attribut data-theme de <html> : les variables CSS
// (src/styles/index.css) et la palette JS (theme.js) changent ensemble.
import { createContext, useContext, useState, useLayoutEffect } from 'react'
import { getTheme, getSavedTheme, saveTheme } from './theme'

const ThemeContext = createContext(null)

export function ThemeProvider({ children }) {
  const [themeName, setThemeName] = useState(getSavedTheme)
  const theme = getTheme(themeName)

  useLayoutEffect(() => {
    saveTheme(themeName)
    document.documentElement.dataset.theme = themeName
  }, [themeName])

  const toggle = () => setThemeName(n => n === 'dark' ? 'light' : 'dark')

  return (
    <ThemeContext.Provider value={{ theme, themeName, toggle }}>
      {children}
    </ThemeContext.Provider>
  )
}

export function useTheme() {
  const ctx = useContext(ThemeContext)
  if (!ctx) throw new Error('useTheme doit être utilisé dans ThemeProvider')
  return ctx
}
