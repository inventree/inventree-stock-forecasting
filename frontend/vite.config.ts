import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vitejs.dev/config/w
export default defineConfig({
  plugins: [react()],
  build: {
    cssCodeSplit: false,
    manifest: false,
    rollupOptions: {
      preserveEntrySignatures: "exports-only",
      input: [
        './src/ForecastingPanel.tsx',
      ],
      output: {
        dir: '../scheduling/static',
        entryFileNames: '[name].js',
        assetFileNames: 'assets/[name].[ext]',
      },
      }
  }
})
