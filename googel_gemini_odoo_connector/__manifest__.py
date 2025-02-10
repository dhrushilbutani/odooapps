# -*- coding: utf-8 -*-
{
    'name': "Odoo Gemini Connector",

    'summary': "The Odoo Gemini Connector integrates Odoo with Google Gemini",

    'description': """
            The Odoo Gemini Connector seamlessly integrates Odoo with Google's powerful Gemini AI. 1  This module empowers users to leverage Gemini's advanced capabilities, such as text generation, translation, summarization, and more, directly within their Odoo environment. 2   By connecting Odoo workflows with Gemini's AI, businesses can automate tasks, improve efficiency, and enhance decision-making.  Key features include customizable prompts, a secure API connection, and seamless integration with existing Odoo processes. 2   This module unlocks the potential of AI within Odoo, enabling users to generate creative content, translate languages effortlessly, and condense information quickly, all without leaving the Odoo platform.   
    """,

    'author': "Dhrushil Butani",
    'maintainer': 'Dhrushil Butani',
    'website': "https://www.yourcompany.com",

   'category': 'Extra Tools',
    'version': '18.0.1.0',
    # 'external_dependencies': {'python': ['google-generativeai']},
    'depends': ['base', 'base_setup', 'mail'],
    'data': [
        'data/gemini_data.xml',
        'views/res_config_setting_view.xml',
        ],
    'images': ['static/description/gemini.png'],
    'installable': True,
    'application': True,
    'auto_install': False,
    'license': 'LGPL-3',

}

