import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  build: {
    cssCodeSplit: false,
    manifest: false,
    rollupOptions: {
      preserveEntrySignatures: "exports-only",
      input: [
        './src/SchedulingPanel.tsx',
      ],
      output: {
        dir: '../scheduling/static',
        entryFileNames: '[name].js',
        assetFileNames: 'assets/[name].[ext]',
      },
      }
  }
})
