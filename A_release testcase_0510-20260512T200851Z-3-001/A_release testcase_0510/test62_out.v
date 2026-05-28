module top (
    n0,
    n1,
    n2,
    n3,
    n4,
    n5,
    n6,
    n7,
    n8,
    n20,
    n22,
    n21,
    n23
);

  input n0;
  input n1;
  input n2;
  input n3;
  input n4;
  input n5;
  input n6;
  input n7;
  input n8;
  output n20;
  output n22;
  output n21;
  output n23;

  wire \$and$testcase/test62/test62.v:9$4_Y , \$auto$rtlil.cc:3255:Not$38 , \$or$testcase/test62/test62.v:10$6_Y , \$xor$testcase/test62/test62.v:11$8_Y , __bal_n21_0__, __bal_n21_1__, __bal_n21_2__, __bal_n21_3__, __bal_n21_4__, __bal_n21_5__, __bal_n21_6__, __bal_n22_0__, __bal_n22_1__, __bal_n22_2__, __bal_n22_3__, __bal_n22_4__, __bal_n22_5__, __bal_n22_6__, __bal_n22_7__, n100, n101, n102, n103, n104, n105, n107, n108, n109, n110, n111, n114, n120, n121, n122, n123, n124, n125, n126, n127, n128, n129, n130, n140, n141, n142;

  and   \$and$testcase/test62/test62.v:13$10  (n107, n2, n3);
  and   \$and$testcase/test62/test62.v:16$13  (n109, n2, n3);
  and   \$and$testcase/test62/test62.v:17$14  (n110, n2, n3);
  and   \$and$testcase/test62/test62.v:27$19  (n120, n2, n3);
  and   \$and$testcase/test62/test62.v:33$25  (n126, n2, n3);
  and   \$and$testcase/test62/test62.v:5$1  (n100, n2, n3);
  and   \$and$testcase/test62/test62.v:28$20  (n121, n2, n4);
  and   \$and$testcase/test62/test62.v:34$26  (n127, n2, n4);
  not   \$not$testcase/test62/test62.v:18$33  (n111, n4);
  not   \$not$testcase/test62/test62.v:21$35  (n114, n4);
  and   \$and$testcase/test62/test62.v:29$21  (n122, n2, n5);
  not   \$auto$opt_expr.cc:605:replace_const_cells$37  (\$auto$rtlil.cc:3255:Not$38 , n5);
  and   \$and$testcase/test62/test62.v:30$22  (n123, n2, n6);
  and   \$and$testcase/test62/test62.v:31$23  (n124, n2, n7);
  and   \$and$testcase/test62/test62.v:37$29  (n140, n6, n7);
  and   \$and$testcase/test62/test62.v:32$24  (n125, n2, n8);
  or    \$or$testcase/test62/test62.v:14$11  (n108, n107, n5);
  or    \$or$testcase/test62/test62.v:6$2  (n101, n100, n4);
  or    \$or$testcase/test62/test62.v:35$27  (n128, n120, n121);
  buf   bal_buf_7 (__bal_n22_0__, n114);
  xor   \$xor$testcase/test62/test62.v:36$28  (n129, n122, n123);
  or    \$or$testcase/test62/test62.v:38$30  (n141, n140, n8);
  buf   bal_buf_0 (__bal_n21_0__, n108);
  not   \$not$testcase/test62/test62.v:7$32  (n102, n101);
  buf   bal_buf_8 (__bal_n22_1__, __bal_n22_0__);
  and   \$and$testcase/test62/test62.v:41$31  (n130, n129, n128);
  not   \$not$testcase/test62/test62.v:39$36  (n142, n141);
  buf   bal_buf_1 (__bal_n21_1__, __bal_n21_0__);
  xor   \$xor$testcase/test62/test62.v:8$3  (n103, n102, n5);
  buf   bal_buf_9 (__bal_n22_2__, __bal_n22_1__);
  buf   bal_buf_2 (__bal_n21_2__, __bal_n21_1__);
  and   \$and$testcase/test62/test62.v:9$4  (\$and$testcase/test62/test62.v:9$4_Y , n103, n6);
  buf   bal_buf_10 (__bal_n22_3__, __bal_n22_2__);
  buf   bal_buf_3 (__bal_n21_3__, __bal_n21_2__);
  not   \$not$testcase/test62/test62.v:9$5  (n104, \$and$testcase/test62/test62.v:9$4_Y );
  buf   bal_buf_11 (__bal_n22_4__, __bal_n22_3__);
  buf   bal_buf_4 (__bal_n21_4__, __bal_n21_3__);
  or    \$or$testcase/test62/test62.v:10$6  (\$or$testcase/test62/test62.v:10$6_Y , n104, n7);
  buf   bal_buf_12 (__bal_n22_5__, __bal_n22_4__);
  buf   bal_buf_5 (__bal_n21_5__, __bal_n21_4__);
  not   \$not$testcase/test62/test62.v:10$7  (n105, \$or$testcase/test62/test62.v:10$6_Y );
  buf   bal_buf_13 (__bal_n22_6__, __bal_n22_5__);
  buf   bal_buf_6 (__bal_n21_6__, __bal_n21_5__);
  xor   \$xor$testcase/test62/test62.v:11$8  (\$xor$testcase/test62/test62.v:11$8_Y , n105, n8);
  buf   bal_buf_14 (__bal_n22_7__, __bal_n22_6__);
  xor   \$xor$testcase/test62/test62.v:15$12  (n21, n6, __bal_n21_6__);
  not   \$not$testcase/test62/test62.v:11$9  (n20, \$xor$testcase/test62/test62.v:11$8_Y );
  or    \$or$testcase/test62/test62.v:23$15  (n22, n100, __bal_n22_7__);

  assign n23 = n5;

endmodule
