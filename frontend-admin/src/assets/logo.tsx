import { type SVGProps } from 'react'
import { cn } from '@/lib/utils'

/** Render the KedaCode logo mark. */
export function Logo({ className, ...props }: SVGProps<SVGSVGElement>) {
  return (
    <svg
      id='keda-logo'
      viewBox='224 224 576 576'
      xmlns='http://www.w3.org/2000/svg'
      height='24'
      width='24'
      fill='none'
      stroke='currentColor'
      strokeWidth='48'
      className={cn('size-6', className)}
      {...props}
    >
      <title>KedaCode</title>
      <path d='M570 290H410c-66 0-120 54-120 120v204c0 66 54 120 120 120h204c66 0 120-54 120-120V454' />
      <path d='M469 567V459h108' strokeLinejoin='round' />
    </svg>
  )
}
