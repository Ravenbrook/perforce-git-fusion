#! /usr/bin/env python3.2

'''
Perforce filetypes handling.
'''

# See 'p4 help filetypes'
#	       Type        Is Base Type  Plus Modifiers
#	      --------    ------------  --------------
ALIASES = {
	      'ctempobj' : ['binary',    'S', 'w'       ]
	    , 'ctext'    : ['text',      'C'            ]
	    , 'cxtext'   : ['text',      'C', 'x'       ]
	    , 'ktext'    : ['text',      'k'            ]
	    , 'kxtext'   : ['text',      'k', 'x'       ]
	    , 'ltext'    : ['text',      'F'            ]
	    , 'tempobj'  : ['binary',    'F', 'S', 'w'  ]
	    , 'ubinary'  : ['binary',    'F'            ]
	    , 'uresource': ['resource',  'F'            ]
	    , 'uxbinary' : ['binary',    'F', 'x'       ]
	    , 'xbinary'  : ['binary',    'x'            ]
	    , 'xltext'   : ['text',      'F', 'x'       ]
	    , 'xtempobj' : ['binary',    'S', 'w', 'x'  ]
	    , 'xtext'    : ['text',      'x'            ]
	    , 'xunicode' : ['unicode',   'x'            ]
	    , 'xutf16'   : ['utf16',     'x'            ]
        }
        
def to_base_mods(filetype):
    '''
    Split a filetype like "xtext" into ["text", "x"]
    
    Invalid filetypes produce undefined results.
    '''
    
    # +S<n> works only because we tear down and rebuild our + mod chars in
    # the same sequence. We actually treat +S10 as +S +1 +0, then rebuild
    # that to +S10 and it just works. Phew.
    
    # Just in case we got 'xtext+k', split off any previous mods.
    base_mod = filetype.split('+')
    mods = base_mod[1:]
    base = base_mod[0]
    if mods:
        # Try again with just the base.
        base_mod = to_base_mods(base)
        mods += base_mod[1:]
        base = base_mod[0]

    if base in ALIASES:
        x = ALIASES[base]
        base = x[0]
        mods += x[1:]
        
    return [ base ] + mods


def remove_mod(filetype, mod):
    '''
    Remove a single modifier such as 'x'
    
    Cannot remove multiple modifiers or +S<n>.
    '''
    if 1 != len(mod):
        raise RuntimeError("Cannot remove multiple modifier chars: {}".format(mod))
        
    base_mods = to_base_mods(filetype)
    if mod in base_mods:
        base_mods.remove(mod)
    
    if 1 == len(base_mods):
        return base_mods[0]
    return "{base}+{mods}".format(base=base_mods[0],
                                  mods="".join(base_mods[1:]))


