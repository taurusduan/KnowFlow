#
#  Copyright 2024 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
import mysql.connector
import os
import tiktoken
import tempfile
import json
from markdown import markdown as md_to_html
import time
import difflib
try:
    from markdown_it import MarkdownIt
    from markdown_it.tree import SyntaxTreeNode
    MARKDOWN_IT_AVAILABLE = True
except ImportError:
    MARKDOWN_IT_AVAILABLE = False
    print("Warning: markdown-it-py not available. Please install with: pip install markdown-it-py")


# 分块模式配置
# CHUNK_METHOD = os.getenv('CHUNK_METHOD', 'smart')  # 默认使用 smart 模式


def get_configured_chunk_method():
    """获取配置的分块方法"""
    method = os.getenv('CHUNK_METHOD', 'smart').lower()  # 每次都重新读取环境变量
    if method in ['advanced', 'smart', 'basic']:
        return method
    else:
        print(f"Warning: Unknown chunk method '{method}', falling back to 'smart'")
        return 'smart'


def is_dev_mode():
    """检查是否启用开发模式"""
    dev_mode = os.getenv('DEV', 'false').lower()
    return dev_mode in ['true', '1', 'yes', 'on']


def should_cleanup_temp_files():
    """检查是否应该清理临时文件"""
    # 在dev模式下，默认不清理临时文件
    if is_dev_mode():
        cleanup = os.getenv('CLEANUP_TEMP_FILES', 'false').lower()
    else:
        cleanup = os.getenv('CLEANUP_TEMP_FILES', 'true').lower()
    
    return cleanup in ['true', '1', 'yes', 'on']


def split_markdown_to_chunks_configured(txt, chunk_token_num=256, min_chunk_tokens=10, **kwargs):
    """
    根据配置选择合适的分块方法的统一接口
    
    支持的分块方法：
    - 'advanced': split_markdown_to_chunks_advanced (高级分块，混合策略)
    - 'smart': split_markdown_to_chunks_smart (智能分块，基于AST，默认)
    - 'basic': split_markdown_to_chunks (基础分块)
    
    可通过环境变量 CHUNK_METHOD 配置，支持的值：advanced, smart, basic
    """
    method = get_configured_chunk_method()
    
    if method == 'advanced':
        # 提取 advanced 方法特有的参数
        include_metadata = kwargs.pop('include_metadata', False)
        overlap_ratio = kwargs.pop('overlap_ratio', 0.0)
        
        return split_markdown_to_chunks_advanced(
            txt, 
            chunk_token_num=chunk_token_num, 
            min_chunk_tokens=min_chunk_tokens,
            overlap_ratio=overlap_ratio,
            include_metadata=include_metadata
        )
    elif method == 'smart':
        return split_markdown_to_chunks_smart(
            txt, 
            chunk_token_num=chunk_token_num, 
            min_chunk_tokens=min_chunk_tokens
        )
    elif method == 'basic':
        delimiter = kwargs.get('delimiter', "\n!?。；！？")
        return split_markdown_to_chunks(
            txt, 
            chunk_token_num=chunk_token_num,
            delimiter=delimiter
        )
    else:
        # 默认回退到 smart 方法
        return split_markdown_to_chunks_smart(
            txt, 
            chunk_token_num=chunk_token_num, 
            min_chunk_tokens=min_chunk_tokens
        )


def singleton(cls, *args, **kw):
    instances = {}

    def _singleton():
        key = str(cls) + str(os.getpid())
        if key not in instances:
            instances[key] = cls(*args, **kw)
        return instances[key]

    return _singleton


tiktoken_cache_dir = tempfile.gettempdir()
os.environ["TIKTOKEN_CACHE_DIR"] = tiktoken_cache_dir
# encoder = tiktoken.encoding_for_model("gpt-3.5-turbo")
encoder = tiktoken.get_encoding("cl100k_base")


def num_tokens_from_string(string: str) -> int:
    """Returns the number of tokens in a text string."""
    try:
        return len(encoder.encode(string))
    except Exception:
        return 0


def truncate(string: str, max_len: int) -> str:
    """Returns truncated text if the length of text exceed max_len."""
    return encoder.decode(encoder.encode(string)[:max_len])


def _extract_tables_and_remainder_md(txt: str) -> (str, list[str]):
    """
    Extracts markdown tables from text and returns the remaining text
    and a list of table strings.
    This is a simplified implementation.
    """
    lines = txt.split('\n')
    tables = []
    remainder_lines = []
    in_table = False
    current_table = []

    for line in lines:
        stripped_line = line.strip()
        # Basic check for table row (starts and ends with |)
        is_table_line = stripped_line.startswith('|') and stripped_line.endswith('|')
        # Basic check for table separator (e.g., |---|---| or |:---|:---:|)
        is_separator_line = True
        if is_table_line and '-' in stripped_line:
            parts = [p.strip() for p in stripped_line[1:-1].split('|')]
            if not all(set(p) <= set('-:') for p in parts if p): # allow only -, :
                is_separator_line = False
            if not parts: # Handles | | case
                is_separator_line = False
        else:
            is_separator_line = False


        if is_table_line or (in_table and stripped_line): # Continue table if already in it and line is not empty
            if not in_table and is_table_line and not is_separator_line: # Potential start of a new table (header)
                # Look ahead for a separator line
                next_line_index = lines.index(line) + 1
                if next_line_index < len(lines):
                    next_line_stripped = lines[next_line_index].strip()
                    next_is_separator = next_line_stripped.startswith('|') and next_line_stripped.endswith('|') and '-' in next_line_stripped
                    if next_is_separator:
                        parts_next = [p.strip() for p in next_line_stripped[1:-1].split('|')]
                        if not all(set(p) <= set('-:') for p in parts_next if p):
                            next_is_separator = False
                        if not parts_next:
                            next_is_separator = False
                    if next_is_separator:
                        in_table = True
                        current_table.append(line)
                    else: # Not a table header
                        remainder_lines.append(line)
                else: # No next line
                     remainder_lines.append(line)
            elif in_table:
                current_table.append(line)
                if not is_table_line and not stripped_line: # Empty line might end the table
                    tables.append("\n".join(current_table))
                    current_table = []
                    in_table = False
                    remainder_lines.append(line) # Add the empty line to remainder
            else: # A line that looks like a table line but isn't starting a valid table
                remainder_lines.append(line)

        elif in_table and not stripped_line : # An empty line definitely ends a table
            tables.append("\n".join(current_table))
            current_table = []
            in_table = False
            remainder_lines.append(line) # Add the empty line to remainder
        elif in_table and not is_table_line : # A non-table line also ends a table
            tables.append("\n".join(current_table))
            current_table = []
            in_table = False
            remainder_lines.append(line) # Add this line to remainder
        else:
            remainder_lines.append(line)

    if current_table: # Add any remaining table
        tables.append("\n".join(current_table))

    return "\n".join(remainder_lines), tables

def split_markdown_to_chunks(txt, chunk_token_num=128, delimiter="\n!?。；！？"):
    """
    Splits markdown text into chunks, processing tables separately and merging text sections
    to be consistent with RAGFlow's naive.py markdown handling.
    """
    if not txt or not txt.strip():
        return []

    # 1. Extract tables and remainder text
    remainder_text, extracted_tables = _extract_tables_and_remainder_md(txt)
    
    processed_chunks = []
    
    # 2. Process tables: convert to HTML and add as individual chunks
    for table_md in extracted_tables:
        if table_md.strip():
            # Ensure markdown.extensions.tables is available
            try:
                table_html = md_to_html(table_md, extensions=['markdown.extensions.tables'])
                processed_chunks.append(table_html)
            except Exception as e:
                # If conversion fails, add raw table markdown as a fallback
                # Or log an error and skip
                processed_chunks.append(table_md)
                print(f"[WARNING] Failed to convert table to HTML: {e}. Added raw table markdown.")


    # 3. Initial splitting of remainder_text (non-table text)
    initial_sections = []
    if remainder_text and remainder_text.strip():
        for sec_line in remainder_text.split("\n"):
            line_content = sec_line.strip()
            if not line_content: # Keep empty lines if they are part of structure or to respect original newlines for merging
                initial_sections.append(sec_line) # Add the original line with its spacing
                continue

            if num_tokens_from_string(sec_line) > 3 * chunk_token_num:
                # Split long lines, trying to preserve original spacing if line was just very long
                mid_point = len(sec_line) // 2
                initial_sections.append(sec_line[:mid_point])
                initial_sections.append(sec_line[mid_point:])
            else:
                initial_sections.append(sec_line)
    
    # 4. Merge initial text sections into chunks respecting token limits (naive_merge logic)
    # This part needs to be careful about document order with tables.
    # The strategy here is to process text between tables.
    # However, _extract_tables_and_remainder_md might not preserve order perfectly if tables are interspersed.
    # For simplicity, we'll process all tables first, then all text. A more sophisticated approach
    # would interleave them based on original position.

    final_text_chunks = []
    current_chunk_parts = []
    current_token_count = 0

    for section_text in initial_sections:
        section_token_count = num_tokens_from_string(section_text)
        
        if not section_text.strip() and not current_chunk_parts: # Skip leading empty/whitespace sections
            continue

        if current_token_count + section_token_count <= chunk_token_num:
            current_chunk_parts.append(section_text)
            current_token_count += section_token_count
        else:
            # Finalize current_chunk if it's not empty
            if current_chunk_parts:
                final_text_chunks.append("\n".join(current_chunk_parts).strip())
            
            # Start a new chunk with the current section
            # If a single section itself is too large, it will be added as is.
            # RAGFlow's naive_merge might have more sophisticated splitting for oversized single sections.
            # For now, we add it as is or split it if it's drastically oversized.
            if section_token_count > chunk_token_num and section_token_count <= 3 * chunk_token_num: # Tolerable oversize
                 final_text_chunks.append(section_text.strip())
                 current_chunk_parts = []
                 current_token_count = 0
            elif section_token_count > 3 * chunk_token_num: # Drastically oversized, needs splitting
                # This split is basic, RAGFlow might be more nuanced
                mid = len(section_text) // 2
                final_text_chunks.append(section_text[:mid].strip())
                final_text_chunks.append(section_text[mid:].strip())
                current_chunk_parts = []
                current_token_count = 0
            else: # Start new chunk
                current_chunk_parts = [section_text]
                current_token_count = section_token_count
    
    # Add any remaining part as the last chunk
    if current_chunk_parts:
        final_text_chunks.append("\n".join(current_chunk_parts).strip())

    # Combine table HTML chunks and text chunks.
    # This simple combination appends all text chunks after all table chunks.
    # A more accurate implementation would require knowing the original order.
    # Given the current _extract_tables_and_remainder_md, this is a limitation.
    all_chunks = [chunk for chunk in processed_chunks if chunk.strip()] # Add table chunks first
    all_chunks.extend([chunk for chunk in final_text_chunks if chunk.strip()])
    
    return all_chunks


_blocks_cache = {}
def get_blocks_from_md(md_file_path):
    if md_file_path in _blocks_cache:
        return _blocks_cache[md_file_path]
    json_path = md_file_path.replace('.md', '_middle.json')
    with open(json_path, 'r') as f:
        data = json.load(f)
        block_list = []
        for page_idx, page in enumerate(data['pdf_info']):
            for block in page['preproc_blocks']:
                bbox = block.get('bbox')
                if not bbox:
                    continue

                if block['type'] in ('text', 'title'):
                    for line in block.get('lines', []):
                        for span in line.get('spans', []):
                            content = span.get('content', '').strip()
                            if content:
                                block_list.append({'content': content, 'bbox': bbox, 'page_number': page_idx })
                
                elif block['type'] == 'table':
                    html_content = ""
                    # Traverse the nested structure to find the HTML content
                    for inner_block in block.get('blocks', []):
                        for line in inner_block.get('lines', []):
                            for span in line.get('spans', []):
                                if 'html' in span:
                                    html_content = span.get('html', '').strip()
                                    if html_content:
                                        break  # Exit spans loop
                            if html_content:
                                break  # Exit lines loop
                        if html_content:
                            break  # Exit inner_blocks loop
                    
                    if html_content:
                        # Use the top-level block's bbox for the entire table
                        block_list.append({'content': html_content, 'bbox': bbox, 'page_number': page_idx })

    _blocks_cache[md_file_path] = block_list
    return block_list

def get_bbox_for_chunk(md_file_path, chunk_content):
    """
    根据 md 文件路径和 chunk 内容，返回构成该 chunk 的连续 block 的 bbox 列表。
    该算法采用混合评分机制寻找最佳"锚点" block，该评分兼顾了最长公共子串的长度和其占 block 自身长度的比例。
    然后从该锚点向前后扩展，寻找同样存在于 chunk 中的连续 block。
    这旨在平衡精确匹配和部分（截断）匹配的场景。
    """
    block_list = get_blocks_from_md(md_file_path)
    if not block_list:
        return None

    chunk_content_clean = chunk_content.strip()
    if not chunk_content_clean:
        return None

    # Step 1: Find anchor block using a hybrid score that balances match length and match ratio.
    best_score = -1.0
    best_match_idx = -1

    for i, block in enumerate(block_list):
        block_content = block.get('content', '').strip()
        if not block_content:
            continue
        
        matcher = difflib.SequenceMatcher(None, block_content, chunk_content_clean, autojunk=False)
        match = matcher.find_longest_match(0, len(block_content), 0, len(chunk_content_clean))
        
        if match.size == 0:
            continue

        lcs_size = match.size
        # The ratio of the match to the block's own length.
        # This rewards matches that are more "complete" for a given block.
        match_ratio = lcs_size / len(block_content)
        
        # The score prioritizes longer matches but is weighted by how much of the block is matched.
        score = lcs_size * match_ratio

        if score > best_score:
            best_score = score
            best_match_idx = i

    # A minimum score to be considered a valid anchor.
    # A score of 10.0 could mean a 10-char match covering 100% of a block,
    # or a 20-char match covering 50% of a 40-char block.
    MIN_ANCHOR_SCORE = 10.0
    if best_match_idx == -1 or best_score < MIN_ANCHOR_SCORE:
        return None

    # Step 2: Expand from the anchor block to find a contiguous sequence.
    # The list of matched blocks will be keyed by index to avoid duplicates.
    matched_blocks = {best_match_idx: block_list[best_match_idx]}

    # Expand backwards from the anchor
    for i in range(best_match_idx - 1, -1, -1):
        block = block_list[i]
        block_content = block.get('content', '').strip()
        if not block_content:
            continue

        matcher = difflib.SequenceMatcher(None, block_content, chunk_content_clean, autojunk=False)
        match = matcher.find_longest_match(0, len(block_content), 0, len(chunk_content_clean))
        
        # Condition for a neighbor block to be considered part of the chunk
        MIN_NEIGHBOR_MATCH_LEN = 10
        MIN_NEIGHBOR_MATCH_RATIO = 0.5 # at least 50% of the block must match
        if match.size > MIN_NEIGHBOR_MATCH_LEN and (match.size / len(block_content)) >= MIN_NEIGHBOR_MATCH_RATIO:
            matched_blocks[i] = block
        else:
            break  # Stop if contiguity is broken

    # Expand forwards from the anchor
    for i in range(best_match_idx + 1, len(block_list)):
        block = block_list[i]
        block_content = block.get('content', '').strip()
        if not block_content:
            continue
        
        matcher = difflib.SequenceMatcher(None, block_content, chunk_content_clean, autojunk=False)
        match = matcher.find_longest_match(0, len(block_content), 0, len(chunk_content_clean))

        MIN_NEIGHBOR_MATCH_LEN = 10
        MIN_NEIGHBOR_MATCH_RATIO = 0.5
        if match.size > MIN_NEIGHBOR_MATCH_LEN and (match.size / len(block_content)) >= MIN_NEIGHBOR_MATCH_RATIO:
            matched_blocks[i] = block
        else:
            break  # Stop if contiguity is broken
    
    # Step 3: Format and return the bboxes for the matched contiguous blocks.
    found_positions = []
    # Sort indices to maintain original document order.
    for idx in sorted(matched_blocks.keys()):
        block = matched_blocks[idx]
        bbox = block.get('bbox')
        page_number = block.get('page_number')
        if bbox and page_number is not None:
            position = [page_number, bbox[0], bbox[2], bbox[1], bbox[3]]
            if position not in found_positions:
                found_positions.append(position)
    
    return found_positions if found_positions else None


def update_document_progress(doc_id, progress=None, message=None, status=None, run=None, chunk_count=None, process_duration=None):
    """更新数据库中文档的进度和状态"""
    conn = None
    cursor = None
    try:
        from database import get_db_connection
        conn = get_db_connection()
        cursor = conn.cursor()
        updates = []
        params = []

        if progress is not None:
            updates.append("progress = %s")
            params.append(float(progress))
        if message is not None:
            updates.append("progress_msg = %s")
            params.append(message)
        if status is not None:
            updates.append("status = %s")
            params.append(status)
        if run is not None:
            updates.append("run = %s")
            params.append(run)
        if chunk_count is not None:
             updates.append("chunk_num = %s")
             params.append(chunk_count)
        if process_duration is not None:
            updates.append("process_duation = %s")
            params.append(process_duration)


        if not updates:
            return

        query = f"UPDATE document SET {', '.join(updates)} WHERE id = %s"
        params.append(doc_id)
        cursor.execute(query, params)
        conn.commit()
    except Exception as e:
        print(f"[Parser-ERROR] 更新文档 {doc_id} 进度失败: {e}")
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def split_markdown_to_chunks_smart(txt, chunk_token_num=256, min_chunk_tokens=10):
    """
    基于 markdown-it-py AST 的智能分块方法，解决 RAG Markdown 文件分块问题：
    1. 基于语义切分（使用 AST）
    2. 维护表格完整性，即使超出了最大 tokens
    3. 考虑 markdown 父子分块关系
    """
    if not MARKDOWN_IT_AVAILABLE:
        print("Warning: markdown-it-py not available, falling back to simple chunking")
        return split_markdown_to_chunks(txt, chunk_token_num)
    
    if not txt or not txt.strip():
        return []

    # 初始化 markdown-it 解析器
    md = MarkdownIt("commonmark", {"breaks": True, "html": True})
    md.enable(['table'])
    
    try:
        # 解析为 AST
        tokens = md.parse(txt)
        tree = SyntaxTreeNode(tokens)
        
        # 基于 AST 进行智能分块
        chunks = []
        current_chunk = []
        current_tokens = 0
        context_stack = []  # 维护标题层级栈
        
        for node in tree.children:
            chunk_data, should_break = _process_ast_node(
                node, context_stack, chunk_token_num, min_chunk_tokens
            )
            
            if should_break and current_chunk and current_tokens >= min_chunk_tokens:
                # 完成当前块
                chunk_content = _finalize_ast_chunk(current_chunk, context_stack)
                if chunk_content.strip():
                    chunks.append(chunk_content)
                current_chunk = []
                current_tokens = 0
            
            if chunk_data:
                chunk_tokens = num_tokens_from_string(chunk_data)
                
                # 检查是否需要分块
                if (current_tokens + chunk_tokens > chunk_token_num and 
                    current_chunk and current_tokens >= min_chunk_tokens):
                    
                    chunk_content = _finalize_ast_chunk(current_chunk, context_stack)
                    if chunk_content.strip():
                        chunks.append(chunk_content)
                    current_chunk = []
                    current_tokens = 0
                
                current_chunk.append(chunk_data)
                current_tokens += chunk_tokens
        
        # 处理最后的块
        if current_chunk:
            chunk_content = _finalize_ast_chunk(current_chunk, context_stack)
            if chunk_content.strip():
                chunks.append(chunk_content)
        
        return [chunk for chunk in chunks if chunk.strip()]
    
    except Exception as e:
        print(f"AST parsing failed: {e}, falling back to simple chunking")
        return split_markdown_to_chunks(txt, chunk_token_num)


def _process_ast_node(node, context_stack, chunk_token_num, min_chunk_tokens):
    """
    处理 AST 节点，返回 (内容, 是否应该分块)
    """
    node_type = node.type
    should_break = False
    content = ""
    
    if node_type == "heading":
        # 标题处理
        level = int(node.tag[1])  # h1 -> 1, h2 -> 2, etc.
        title_text = _extract_text_from_node(node)
        
        # 更新上下文栈
        _update_context_stack(context_stack, level, title_text)
        
        content = node.markup + " " + title_text
        should_break = True  # 标题通常作为分块边界
        
    elif node_type == "table":
        # 表格处理 - 保持完整性
        content = _render_table_from_ast(node)
        table_tokens = num_tokens_from_string(content)
        
        # 表格过大时也要保持完整性
        if table_tokens > chunk_token_num:
            should_break = True
            
    elif node_type == "code_block":
        # 代码块处理
        content = f"```{node.info or ''}\n{node.content}```"
        
    elif node_type == "blockquote":
        # 引用块处理
        content = _render_blockquote_from_ast(node)
        
    elif node_type == "list":
        # 列表处理
        content = _render_list_from_ast(node)
        
    elif node_type == "paragraph":
        # 段落处理
        content = _extract_text_from_node(node)
        
    elif node_type == "hr":
        # 分隔符
        content = "---"
        should_break = True
        
    else:
        # 其他类型节点
        content = _extract_text_from_node(node)
    
    return content, should_break


def _update_context_stack(context_stack, level, title):
    """更新标题上下文栈"""
    # 移除比当前级别更深的标题
    while context_stack and context_stack[-1]['level'] >= level:
        context_stack.pop()
    
    # 添加当前标题
    context_stack.append({'level': level, 'title': title})


def _extract_text_from_node(node):
    """从 AST 节点提取文本内容"""
    if hasattr(node, 'content') and node.content:
        return node.content
    
    text_parts = []
    if hasattr(node, 'children') and node.children:
        for child in node.children:
            if child.type == "text":
                text_parts.append(child.content)
            elif child.type == "code_inline":
                text_parts.append(f"`{child.content}`")
            elif child.type == "strong":
                text_parts.append(f"**{_extract_text_from_node(child)}**")
            elif child.type == "em":
                text_parts.append(f"*{_extract_text_from_node(child)}*")
            elif child.type == "link":
                link_text = _extract_text_from_node(child)
                text_parts.append(f"[{link_text}]({child.attrGet('href') or ''})")
            else:
                text_parts.append(_extract_text_from_node(child))
    
    return "".join(text_parts)


def _render_table_from_ast(table_node):
    """从 AST 渲染表格为 HTML"""
    try:
        # 构建表格的 markdown 表示
        table_md = []
        
        for child in table_node.children:
            if child.type == "thead":
                # 表头处理
                for row in child.children:
                    if row.type == "tr":
                        cells = []
                        for cell in row.children:
                            if cell.type in ["th", "td"]:
                                cells.append(_extract_text_from_node(cell))
                        table_md.append("| " + " | ".join(cells) + " |")
                
                # 添加分隔符
                if table_md:
                    separator = "| " + " | ".join(["---"] * len(cells)) + " |"
                    table_md.append(separator)
                    
            elif child.type == "tbody":
                # 表体处理
                for row in child.children:
                    if row.type == "tr":
                        cells = []
                        for cell in row.children:
                            if cell.type in ["th", "td"]:
                                cells.append(_extract_text_from_node(cell))
                        table_md.append("| " + " | ".join(cells) + " |")
        
        # 转换为 HTML
        table_markdown = "\n".join(table_md)
        return md_to_html(table_markdown, extensions=['markdown.extensions.tables'])
        
    except Exception as e:
        print(f"Table rendering error: {e}")
        return _extract_text_from_node(table_node)


def _render_list_from_ast(list_node):
    """从 AST 渲染列表"""
    list_items = []
    list_type = list_node.attrGet('type') or 'bullet'
    
    for i, item in enumerate(list_node.children):
        if item.type == "list_item":
            item_content = _extract_text_from_node(item)
            if list_type == 'ordered':
                list_items.append(f"{i+1}. {item_content}")
            else:
                list_items.append(f"- {item_content}")
    
    return "\n".join(list_items)


def _render_blockquote_from_ast(blockquote_node):
    """从 AST 渲染引用块"""
    content = _extract_text_from_node(blockquote_node)
    lines = content.split('\n')
    return '\n'.join(f"> {line}" for line in lines)


def _finalize_ast_chunk(chunk_parts, context_stack):
    """完成基于 AST 的 chunk 格式化"""
    chunk_content = "\n\n".join(chunk_parts).strip()
    
    # 可以根据需要添加上下文信息
    # 例如，如果chunk没有标题，可以考虑添加父级标题作为上下文
    
    return chunk_content


def split_markdown_to_chunks_advanced(txt, chunk_token_num=256, min_chunk_tokens=10, 
                                     overlap_ratio=0.0, include_metadata=False):
    """
    基于标题层级的高级 Markdown 分块方法 (混合分块策略 + 动态阈值调整)
    
    核心特性：
    1. 保持标题作为主要分块边界
    2. 动态大小控制：目标300-600 tokens，最大800 tokens，最小50 tokens  
    3. 处理超大分块：在段落边界进一步分割
    4. 处理超小分块：与相邻分块合并
    5. 特殊内容处理：保持表格、代码块、公式完整性
    6. 智能上下文增强
    """
    if not MARKDOWN_IT_AVAILABLE:
        return split_markdown_to_chunks(txt, chunk_token_num)
    
    if not txt or not txt.strip():
        return []

    # 动态阈值配置
    target_min_tokens = max(50, min_chunk_tokens // 2)  # 最小50 tokens
    target_tokens = min(600, chunk_token_num)  # 目标大小：300-600 tokens
    target_max_tokens = min(800, chunk_token_num * 1.5)  # 最大800 tokens
    
    # 配置要作为分块边界的标题级别
    headers_to_split_on = [1, 2, 3]  # H1, H2, H3 作为分块边界
    
    # 初始化 markdown-it 解析器
    md = MarkdownIt("commonmark", {"breaks": True, "html": True})
    md.enable(['table'])
    
    try:
        # 解析为 AST
        tokens = md.parse(txt)
        tree = SyntaxTreeNode(tokens)
        
        # 提取所有节点和标题信息
        nodes_with_headers = _extract_nodes_with_header_info(tree, headers_to_split_on)
        
        # 基于标题层级进行初步分块
        initial_chunks = _split_by_header_levels(nodes_with_headers, headers_to_split_on)
        
        # 应用动态大小控制和优化
        optimized_chunks = _apply_size_control_and_optimization(
            initial_chunks, target_min_tokens, target_tokens, target_max_tokens
        )
        
        # 生成最终分块内容
        final_chunks = []
        for chunk_info in optimized_chunks:
            content = _render_header_chunk_advanced(chunk_info)
            if content.strip():
                if include_metadata:
                    chunk_data = {
                        'content': content,
                        'metadata': chunk_info.get('headers', {}),
                        'token_count': num_tokens_from_string(content),
                        'chunk_type': chunk_info.get('chunk_type', 'header_based'),
                        'has_special_content': chunk_info.get('has_special_content', False),
                        'source_sections': chunk_info.get('source_sections', 1)
                    }
                    final_chunks.append(chunk_data)
                else:
                    final_chunks.append(content)
        
        return final_chunks
    
    except Exception as e:
        print(f"Advanced header-based parsing failed: {e}, falling back to smart chunking")
        return split_markdown_to_chunks_smart(txt, chunk_token_num, min_chunk_tokens)


def _apply_size_control_and_optimization(chunks, min_tokens, target_tokens, max_tokens):
    """应用动态大小控制和优化策略"""
    optimized_chunks = []
    
    i = 0
    while i < len(chunks):
        chunk = chunks[i]
        chunk_content = _render_header_chunk(chunk)
        chunk_tokens = num_tokens_from_string(chunk_content)
        
        # 检查特殊内容类型
        has_special_content = _has_special_content(chunk)
        
        if chunk_tokens <= max_tokens and chunk_tokens >= min_tokens:
            # 大小合适，直接添加
            chunk['chunk_type'] = 'normal'
            chunk['has_special_content'] = has_special_content
            optimized_chunks.append(chunk)
            
        elif chunk_tokens > max_tokens and not has_special_content:
            # 超大分块，需要进一步分割（除非包含特殊内容）
            split_chunks = _split_oversized_chunk(chunk, target_tokens, max_tokens)
            optimized_chunks.extend(split_chunks)
            
        elif chunk_tokens < min_tokens:
            # 超小分块，尝试与下一个分块合并
            merged_chunk = _try_merge_with_next(chunk, chunks, i, target_tokens)
            if merged_chunk:
                optimized_chunks.append(merged_chunk)
                # 跳过被合并的分块
                i += merged_chunk.get('merged_count', 1) - 1
            else:
                # 无法合并，添加上下文增强
                enhanced_chunk = _enhance_small_chunk_with_context(chunk)
                optimized_chunks.append(enhanced_chunk)
        else:
            # 包含特殊内容的超大分块，保持完整性但添加标记
            chunk['chunk_type'] = 'oversized_special'
            chunk['has_special_content'] = has_special_content
            optimized_chunks.append(chunk)
        
        i += 1
    
    return optimized_chunks


def _has_special_content(chunk):
    """检查分块是否包含特殊内容（表格、代码块、公式等）"""
    for node_info in chunk.get('nodes', []):
        node_type = node_info.get('type', '')
        content = node_info.get('content', '')
        
        # 检查特殊内容类型
        if node_type in ['table', 'code_block']:
            return True
        
        # 检查数学公式
        if '$$' in content or '$' in content:
            return True
            
        # 检查HTML表格
        if '<table>' in content and '</table>' in content:
            return True
            
    return False


def _split_oversized_chunk(chunk, target_tokens, max_tokens):
    """分割超大分块，在段落边界进行分割"""
    split_chunks = []
    nodes = chunk.get('nodes', [])
    headers = chunk.get('headers', {})
    
    current_nodes = []
    current_tokens = 0
    
    for node_info in nodes:
        node_content = node_info.get('content', '')
        node_tokens = num_tokens_from_string(node_content)
        
        # 检查是否是标题节点
        is_heading = node_info.get('type') == 'heading'
        
        # 如果当前节点会导致超出目标大小，且当前已有内容
        if current_tokens + node_tokens > target_tokens and current_nodes:
            # 创建一个分块
            new_chunk = {
                'headers': headers.copy(),
                'nodes': current_nodes.copy(),
                'chunk_type': 'split_from_oversized',
                'has_special_content': any(_has_special_content({'nodes': [n]}) for n in current_nodes)
            }
            split_chunks.append(new_chunk)
            
            # 开始新分块
            current_nodes = [node_info]
            current_tokens = node_tokens
            
            # 如果是标题，更新headers上下文
            if is_heading:
                level = node_info.get('level', 3)
                title = node_info.get('title', '')
                new_headers = {k: v for k, v in headers.items() if k < level}
                new_headers[level] = title
                headers = new_headers
        else:
            current_nodes.append(node_info)
            current_tokens += node_tokens
            
            # 更新标题上下文
            if is_heading:
                level = node_info.get('level', 3)
                title = node_info.get('title', '')
                headers = {k: v for k, v in headers.items() if k < level}
                headers[level] = title
    
    # 添加最后一个分块
    if current_nodes:
        final_chunk = {
            'headers': headers.copy(),
            'nodes': current_nodes,
            'chunk_type': 'split_from_oversized',
            'has_special_content': any(_has_special_content({'nodes': [n]}) for n in current_nodes)
        }
        split_chunks.append(final_chunk)
    
    return split_chunks


def _try_merge_with_next(current_chunk, all_chunks, current_index, target_tokens):
    """尝试将小分块与后续分块合并"""
    if current_index >= len(all_chunks) - 1:
        return None
    
    next_chunk = all_chunks[current_index + 1]
    
    # 计算合并后的大小
    current_content = _render_header_chunk(current_chunk)
    next_content = _render_header_chunk(next_chunk)
    merged_tokens = num_tokens_from_string(current_content + "\n\n" + next_content)
    
    # 如果合并后大小合适
    if merged_tokens <= target_tokens * 1.2:  # 允许轻微超出目标大小
        merged_chunk = {
            'headers': next_chunk.get('headers', current_chunk.get('headers', {})),
            'nodes': current_chunk.get('nodes', []) + next_chunk.get('nodes', []),
            'chunk_type': 'merged_small',
            'has_special_content': (_has_special_content(current_chunk) or 
                                  _has_special_content(next_chunk)),
            'merged_count': 2,
            'source_sections': 2
        }
        return merged_chunk
    
    return None


def _enhance_small_chunk_with_context(chunk):
    """为小分块增强上下文信息"""
    enhanced_chunk = chunk.copy()
    enhanced_chunk['chunk_type'] = 'small_enhanced'
    enhanced_chunk['has_special_content'] = _has_special_content(chunk)
    
    # 确保包含足够的标题上下文
    headers = chunk.get('headers', {})
    if headers:
        # 添加完整的标题路径作为上下文
        context_parts = []
        for level in sorted(headers.keys()):
            context_parts.append(f"{'#' * level} {headers[level]}")
        
        # 在节点前添加上下文信息
        if context_parts:
            context_node = {
                'type': 'context',
                'content': '\n'.join(context_parts),
                'headers': headers.copy(),
                'is_split_boundary': False
            }
            enhanced_chunk['nodes'] = [context_node] + enhanced_chunk.get('nodes', [])
    
    return enhanced_chunk


def _render_header_chunk_advanced(chunk_info):
    """高级渲染基于标题的分块内容，包含更好的格式化"""
    content_parts = []
    
    # 处理标题上下文
    chunk_has_header = any(node['type'] == 'heading' for node in chunk_info.get('nodes', []))
    headers = chunk_info.get('headers', {})
    
    # 为某些类型的分块添加标题上下文
    chunk_type = chunk_info.get('chunk_type', 'normal')
    if chunk_type in ['split_from_oversized', 'small_enhanced'] and headers and not chunk_has_header:
        # 添加最相关的上下文标题
        context_header = _get_most_relevant_header_advanced(headers, chunk_type)
        if context_header:
            content_parts.append(context_header)
    
    # 渲染所有节点内容（移除标记，保持内容干净）
    for node_info in chunk_info.get('nodes', []):
        if node_info.get('content', '').strip():
            content = node_info['content']
            # 直接使用原始内容，不添加任何标记
            content_parts.append(content)
    
    result = "\n\n".join(content_parts).strip()
    
    # 移除重叠分块的标识，保持内容干净
    # if chunk_type == 'overlap':
    #     result = f"[上下文关联内容]\n{result}"
    
    return result


def _get_most_relevant_header_advanced(headers, chunk_type):
    """获取最相关的上下文标题（高级版本）"""
    if not headers:
        return None
    
    # 根据分块类型选择不同的上下文策略
    if chunk_type == 'split_from_oversized':
        # 分割分块：显示最深层级的标题
        max_level = max(headers.keys())
        return f"{'#' * max_level} {headers[max_level]}"
    
    elif chunk_type in ['small_enhanced']:
        # 增强分块：显示最相关的标题
        max_level = max(headers.keys())
        return f"{'#' * max_level} {headers[max_level]}"
    
    else:
        # 普通分块：显示最相关的标题
        max_level = max(headers.keys())
        return f"{'#' * max_level} {headers[max_level]}"


def optimize_chunks_for_rag(chunks, target_vector_dim=1536):
    """
    基础RAG分块优化，为向量化做准备
    """
    optimized_chunks = []
    
    for chunk_data in chunks:
        if isinstance(chunk_data, str):
            chunk_data = {'content': chunk_data, 'token_count': num_tokens_from_string(chunk_data)}
        
        optimized_chunks.append(chunk_data)
    
    return optimized_chunks

def _extract_nodes_with_header_info(tree, headers_to_split_on):
    """提取所有节点及其对应的标题信息"""
    nodes_with_headers = []
    current_headers = {}  # 当前的标题层级路径
    
    for node in tree.children:
        if node.type == "heading":
            level = int(node.tag[1])  # h1 -> 1, h2 -> 2, etc.
            title = _extract_text_from_node(node)
            
            # 更新当前标题路径
            # 移除比当前级别更深的标题
            current_headers = {k: v for k, v in current_headers.items() if k < level}
            # 添加当前标题
            current_headers[level] = title
            
            # 如果是分块边界标题，标记为分块起始点
            is_split_boundary = level in headers_to_split_on
            
            nodes_with_headers.append({
                'node': node,
                'type': 'heading',
                'level': level,
                'title': title,
                'headers': current_headers.copy(),
                'is_split_boundary': is_split_boundary,
                'content': node.markup + " " + title
            })
        else:
            # 非标题节点
            content = _render_node_content(node)
            if content.strip():
                nodes_with_headers.append({
                    'node': node,
                    'type': node.type,
                    'headers': current_headers.copy(),
                    'is_split_boundary': False,
                    'content': content
                })
    
    return nodes_with_headers


def _render_node_content(node):
    """渲染单个节点的内容"""
    if node.type == "table":
        return _render_table_from_ast(node)
    elif node.type == "code_block":
        return f"```{node.info or ''}\n{node.content}```"
    elif node.type == "blockquote":
        return _render_blockquote_from_ast(node)
    elif node.type in ["bullet_list", "ordered_list"]:
        return _render_list_from_ast(node)
    elif node.type == "paragraph":
        return _extract_text_from_node(node)
    elif node.type == "hr":
        return "---"
    else:
        return _extract_text_from_node(node)


def _split_by_header_levels(nodes_with_headers, headers_to_split_on):
    """基于标题层级进行分块，智能处理连续标题"""
    chunks = []
    current_chunk = {
        'headers': {},
        'nodes': []
    }
    
    i = 0
    while i < len(nodes_with_headers):
        node_info = nodes_with_headers[i]
        
        # 检查是否为分块边界标题
        if node_info['is_split_boundary']:
            # 先检查是否为连续短标题的情况
            if node_info['type'] == 'heading':
                current_title = node_info.get('title', '').strip()
                
                # 检查当前标题是否很短（可能只是编号）
                is_short_title = (
                    len(current_title) <= 12 and 
                    (
                        # 纯数字编号如 "3.7", "4.1"
                        (current_title.replace('.', '').replace(' ', '').isdigit()) or
                        # 短编号如 "3.7", "4", "A.1"  
                        (len(current_title.split()) <= 2 and 
                         any(char.isdigit() for char in current_title))
                    )
                )
                
                # 如果是短标题，向前查找看是否有紧跟的内容标题
                if is_short_title:
                    # 查找接下来的几个节点，看是否有实质性内容标题
                    found_content_header = False
                    j = i + 1
                    
                    # 向前查看最多3个节点
                    while j < len(nodes_with_headers) and j < i + 4:
                        next_node = nodes_with_headers[j]
                        
                        # 如果找到另一个标题
                        if next_node.get('type') == 'heading':
                            next_title = next_node.get('title', '').strip()
                            
                            # 检查是否为更有实质内容的标题
                            is_content_header = (
                                len(next_title) > 12 or  # 较长的标题
                                (len(next_title.split()) > 2) or  # 多个词
                                any(word for word in next_title.split() 
                                    if len(word) > 3 and not word.replace('.', '').isdigit())  # 有非数字词汇
                            )
                            
                            if is_content_header:
                                found_content_header = True
                                break
                        
                        # 如果遇到其他内容，停止查找
                        elif next_node.get('content', '').strip():
                            break
                        
                        j += 1
                    
                    # 如果找到了内容标题，跳过当前标题的分块处理
                    if found_content_header:
                        # 直接添加到当前块，不作为分块边界
                        current_chunk['nodes'].append(node_info)
                        i += 1
                        continue
            
            # 正常的分块边界处理
            # 完成当前块（如果有内容）
            if (current_chunk['nodes'] and 
                any(n for n in current_chunk['nodes'] if n['content'].strip())):
                chunks.append(current_chunk)
                current_chunk = {
                    'headers': {},
                    'nodes': []
                }
        
        # 更新当前块的标题信息
        if node_info['headers']:
            current_chunk['headers'] = node_info['headers'].copy()
        
        # 添加节点到当前块
        current_chunk['nodes'].append(node_info)
        i += 1
    
    # 添加最后一个块
    if current_chunk['nodes'] and any(n for n in current_chunk['nodes'] if n['content'].strip()):
        chunks.append(current_chunk)
    
    return chunks


def _render_header_chunk(chunk_info):
    """渲染基于标题的分块内容（原始版本，用于兼容性）"""
    content_parts = []
    
    # 添加标题上下文（如果分块本身不包含标题）
    chunk_has_header = any(node['type'] == 'heading' for node in chunk_info.get('nodes', []))
    
    if not chunk_has_header and chunk_info.get('headers'):
        # 添加最相关的上下文标题
        context_header = _get_most_relevant_header(chunk_info['headers'])
        if context_header:
            content_parts.append(context_header)
    
    # 渲染所有节点内容
    for node_info in chunk_info.get('nodes', []):
        if node_info.get('content', '').strip():
            content_parts.append(node_info['content'])
    
    return "\n\n".join(content_parts).strip()


def _get_most_relevant_header(headers):
    """获取最相关的上下文标题（原始版本）"""
    if not headers:
        return None
    
    # 选择最深层级的标题作为上下文
    max_level = max(headers.keys())
    return f"{'#' * max_level} {headers[max_level]}"